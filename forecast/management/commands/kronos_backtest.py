from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand

from forecast.adapter import bars_to_payload, invoke_kronos, load_kronos_settings, next_session_timestamps
from forecast.stats import path_outcomes

IST = ZoneInfo("Asia/Kolkata")
# Kronos paper: walk-forward only from this date (earlier may be in pretraining).
DEFAULT_START = date(2025, 9, 1)


class Command(BaseCommand):
    help = (
        "Sampled Kronos walk-forward backtest (post-2025-09-01 only). "
        "Reports measured hit rates — no accuracy claims."
    )

    def add_arguments(self, parser):
        parser.add_argument("--symbol", default="RELIANCE")
        parser.add_argument("--start", default=DEFAULT_START.isoformat())
        parser.add_argument("--end", default="")
        parser.add_argument("--every", type=int, default=5, help="Evaluate every N sessions")
        parser.add_argument("--horizon", type=int, default=5)
        parser.add_argument("--paths", type=int, default=16)
        parser.add_argument("--lookback", type=int, default=400)

    def handle(self, *args, **opts):
        from marketdata.service import MarketDataService

        start = date.fromisoformat(opts["start"])
        if start < DEFAULT_START:
            self.stdout.write(
                self.style.WARNING(
                    f"Clamping start to {DEFAULT_START} (pretraining contamination risk)"
                )
            )
            start = DEFAULT_START
        end = date.fromisoformat(opts["end"]) if opts["end"] else date.today()
        sym = opts["symbol"].upper().replace(".NS", "")
        hist_sym = f"{sym}.NS"
        svc = MarketDataService()
        df = svc.bars_df(hist_sym, interval="1d")
        if df is None or df.empty:
            self.stderr.write(self.style.ERROR(f"no bars for {hist_sym}"))
            return

        settings_k = load_kronos_settings()
        every = max(1, int(opts["every"]))
        horizon = int(opts["horizon"])
        lookback = int(opts["lookback"])
        paths_n = int(opts["paths"])

        # Index sessions on/after start
        idx = df.index
        if hasattr(idx, "tz_localize"):
            try:
                idx = idx.tz_convert("Asia/Kolkata")
            except Exception:  # noqa: BLE001
                pass
        dates = [d.date() if hasattr(d, "date") else d for d in idx]
        eval_points = [
            i
            for i, d in enumerate(dates)
            if d >= start and d <= end and i >= lookback and (i - lookback) % every == 0
        ]
        if not eval_points:
            self.stderr.write(self.style.ERROR("no evaluation points in range"))
            return

        hits_up = 0
        n = 0
        tgt_hits = 0
        stp_hits = 0
        for i in eval_points:
            window = df.iloc[max(0, i - lookback) : i]
            if len(window) < 60:
                continue
            entry = float(window["close"].iloc[-1])
            # Simple ATR-ish levels for measured target/stop race
            atr = float((window["high"] - window["low"]).tail(14).mean() or entry * 0.01)
            stop = entry - 1.0 * atr
            target = entry + 1.5 * atr
            future = df.iloc[i : i + horizon]
            if len(future) < horizon:
                continue

            bars = bars_to_payload(window, lookback=lookback)
            y_ts = next_session_timestamps(bars[-1]["ts"], horizon, interval="1d")
            req = {
                "action": "predict",
                "bars": bars,
                "y_timestamps": y_ts,
                "pred_len": horizon,
                "lookback": lookback,
                "paths": paths_n,
                "batch_chunk": min(8, paths_n),
                "T": settings_k["T"],
                "top_p": settings_k["top_p"],
                "seed": settings_k["seed"],
                "model_id": settings_k["model_id"],
                "tokenizer_id": settings_k["tokenizer_id"],
                "max_context": settings_k["max_context"],
                "device": settings_k["device"],
                "hf_cache": settings_k["hf_cache"],
            }
            result = invoke_kronos(req, timeout_sec=settings_k["timeout_sec"])
            if not result.get("ok"):
                self.stdout.write(f"skip {dates[i]}: {result.get('error')}")
                continue
            stats = path_outcomes(
                result.get("paths") or [],
                entry=entry,
                stop=stop,
                target=target,
                side="BUY",
            )
            actual_close = float(future["close"].iloc[-1])
            pred_up = (stats.get("p_up") or 0) >= 0.5
            actual_up = actual_close > entry
            if pred_up == actual_up:
                hits_up += 1
            # Realized target/stop on actual path
            realized = 0
            for _, row in future.iterrows():
                if float(row["low"]) <= stop and float(row["high"]) >= target:
                    realized = -1
                    break
                if float(row["low"]) <= stop:
                    realized = -1
                    break
                if float(row["high"]) >= target:
                    realized = 1
                    break
            if realized == 1:
                tgt_hits += 1
            elif realized == -1:
                stp_hits += 1
            n += 1
            self.stdout.write(
                f"{dates[i]} p_up={stats.get('p_up')} call={stats.get('call')} "
                f"actual_up={actual_up} realized={realized}"
            )

        if n == 0:
            self.stderr.write(self.style.ERROR("no completed evaluations"))
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"MEASURED Kronos {sym} n={n} direction_hit_rate={hits_up/n:.3f} "
                f"target_first_rate={tgt_hits/n:.3f} stop_first_rate={stp_hits/n:.3f} "
                f"(every={every}, horizon={horizon}, paths={paths_n}, start={start})"
            )
        )
