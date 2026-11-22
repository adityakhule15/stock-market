from __future__ import annotations

import logging

from config import trading_config as cfg
from features.builders import build_dataset, feature_matrix
from marketdata.models import Instrument
from marketdata.service import MarketDataService
from marketdata.universe import DEFAULT_UNIVERSE, shortlist_symbols
from ml import registry
from ml.evaluate import evaluate_walk_forward
from ml.train import fit_lgbm, optuna_search, time_train_test_split, walk_forward_train

logger = logging.getLogger(__name__)


def _training_symbols(limit: int = 50) -> list[str]:
    with_bars = list(
        Instrument.objects.filter(bars__isnull=False)
        .distinct()
        .values_list("symbol", flat=True)[:limit]
    )
    if len(with_bars) >= 5:
        merged = list(dict.fromkeys(list(with_bars) + list(DEFAULT_UNIVERSE)))
        return merged[:limit]
    short = shortlist_symbols(limit)
    return short or list(DEFAULT_UNIVERSE[:limit])


def _combined_dataset(symbols: list[str] | None = None):
    svc = MarketDataService()
    symbols = symbols or _training_symbols(40)
    frames = []
    for sym in symbols:
        try:
            df = svc.bars_df(sym, interval="1d")
            if df is None or len(df) < 120:
                continue
            ds = build_dataset(df, atr_mult_sl=cfg.ATR_SL_MULT, rr=cfg.T1_R, horizon=5)
            if ds.empty or ds["y"].nunique() < 2:
                continue
            ds["symbol"] = sym
            frames.append(ds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dataset skip %s: %s", sym, exc)
    if not frames:
        raise RuntimeError("No training data available — run backfill first")
    import pandas as pd

    out = pd.concat(frames, axis=0)
    out = out.sort_index()
    return out


def retrain_and_maybe_promote(
    name: str = "swing_lgbm",
    full: bool = False,
    n_trials: int = 30,
    *,
    retire_legacy: bool = False,
):
    """Train + promote only on honest holdout: beats baseline log-loss AND positive expectancy.

    Never promote just because a candidate is 'less bad' than a losing champion.
    """
    df = _combined_dataset()
    params = None
    if full:
        params = optuna_search(df, n_trials=n_trials)

    candidate_bundle, cand_m = walk_forward_train(df, params=params)
    hold = cand_m.get("holdout") or {}
    for k in ("expectancy_R", "max_dd", "brier", "n_signals", "auc", "hit_rate", "log_loss", "baseline_log_loss"):
        if k in hold:
            cand_m[k] = hold[k]
    cand_m["train_start"] = df.index.min().date()
    cand_m["train_end"] = df.index.max().date()
    cand_m["n_symbols"] = int(df["symbol"].nunique()) if "symbol" in df.columns else 0

    _, test_df = time_train_test_split(df, test_ratio=0.2, embargo=8)
    champ = registry.champion(name)

    # One-shot: retire in-sample champions (pre-honest metrics)
    if retire_legacy and champ is not None:
        old_m = champ.metrics if isinstance(champ.metrics, dict) else {}
        if not old_m.get("honest"):
            logger.info("Retiring legacy in-sample champion %s_v%s", name, champ.version)
            champ.status = "retired"
            champ.save(update_fields=["status"])
            champ = None

    cand_exp = float(cand_m.get("expectancy_R") or 0)
    cand_ll = cand_m.get("log_loss")
    base_ll = cand_m.get("baseline_log_loss")
    cand_n = int(cand_m.get("n_signals") or 0)

    beats_baseline = (
        cand_ll is not None
        and base_ll is not None
        and float(cand_ll) < float(base_ll) - 1e-6
    )
    positive_edge = cand_exp > 0 and cand_n >= 10
    honest = bool(cand_m.get("honest"))

    trustworthy = honest and beats_baseline and positive_edge
    cand_m["trustworthy"] = trustworthy
    promote = False
    provisional = False
    if champ is None:
        # Absolute bar for a *trusted* champion
        promote = trustworthy
        if not promote and honest:
            # Desktop / offline: still install as provisional champion so scans
            # can run, while UI shows "no trustworthy model".
            provisional = True
            cand_m["provisional"] = True
            cand_m["no_trustworthy_model"] = True
            logger.info(
                "No trusted edge (exp=%.3f n=%s) — installing provisional honest champion",
                cand_exp,
                cand_n,
            )
        elif not promote:
            logger.info(
                "No champion; candidate not promoted (honest=%s beats_base=%s exp=%.3f n=%s)",
                honest,
                beats_baseline,
                cand_exp,
                cand_n,
            )
    else:
        champ_bundle = registry.load(champ)
        eval_df = test_df if len(test_df) >= 40 else df
        champ_m = evaluate_walk_forward(
            champ_bundle, eval_df, last_n_sessions=min(120, len(eval_df))
        )
        champ_exp = float(champ_m.get("expectancy_R") or 0)
        # Candidate must clear absolute bar AND beat champion on holdout expectancy
        promote = (
            trustworthy
            and cand_exp >= champ_exp + 0.02
            and cand_m.get("brier", 1) <= champ_m.get("brier", 1) + 0.02
        )
        # Replace a provisional/untrusted champion with a newer honest candidate
        # even without edge, so metrics stay current (still marked untrusted).
        if (
            not promote
            and honest
            and not (champ.metrics or {}).get("trustworthy", True)
            and float(cand_m.get("log_loss") or 9) <= float((champ.metrics or {}).get("log_loss") or 9) + 1e-6
        ):
            provisional = True
            cand_m["provisional"] = True
            cand_m["no_trustworthy_model"] = True

    mv = registry.save(candidate_bundle, metrics=cand_m, name=name, status="candidate")
    if promote or provisional:
        registry.promote(mv)
        logger.info(
            "Promoted %s_v%s trustworthy=%s provisional=%s metrics=%s",
            name,
            mv.version,
            trustworthy,
            provisional,
            {k: cand_m.get(k) for k in ("expectancy_R", "hit_rate", "log_loss")},
        )
    else:
        logger.info("Kept champion; candidate %s_v%s not better / failed bar", name, mv.version)
    return mv, promote or provisional


def maybe_auto_rollback(name: str = "swing_lgbm") -> bool:
    """Prefer shadow-book outcomes; fall back to closed signals."""
    try:
        from evidence.stats import shadow_hit_rate

        hr = shadow_hit_rate(kind="stocks", lookback=cfg.ROLLBACK_LOOKBACK_SIGNALS)
        if hr is not None and hr["n"] >= cfg.ROLLBACK_LOOKBACK_SIGNALS and hr["hit_rate"] < cfg.ROLLBACK_HIT_RATE:
            registry.rollback(name)
            return True
    except Exception:  # noqa: BLE001
        pass

    from signals.models import Signal

    recent = list(
        Signal.objects.filter(status__in=["closed", "stopped", "target"])
        .order_by("-created_at")[: cfg.ROLLBACK_LOOKBACK_SIGNALS]
    )
    if len(recent) < cfg.ROLLBACK_LOOKBACK_SIGNALS:
        return False
    hits = sum(1 for s in recent if s.status == "target")
    hit_rate = hits / len(recent)
    if hit_rate < cfg.ROLLBACK_HIT_RATE:
        registry.rollback(name)
        return True
    return False
