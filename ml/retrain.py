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
    # Prefer symbols that already have bars; fall back to default universe
    with_bars = list(
        Instrument.objects.filter(bars__isnull=False)
        .distinct()
        .values_list("symbol", flat=True)[:limit]
    )
    if len(with_bars) >= 5:
        # Merge with DEFAULT_UNIVERSE so newly seeded NSE names get included after backfill
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
            ds = build_dataset(df)
            if ds.empty or ds["y"].nunique() < 2:
                continue
            ds["symbol"] = sym
            frames.append(ds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dataset skip %s: %s", sym, exc)
    if not frames:
        raise RuntimeError("No training data available — run backfill first")
    import pandas as pd

    # Stack symbols; keep time order for chronological train/test split
    out = pd.concat(frames, axis=0)
    out = out.sort_index()
    return out


def retrain_and_maybe_promote(name: str = "swing_lgbm", full: bool = False, n_trials: int = 30):
    df = _combined_dataset()
    params = None
    if full:
        params = optuna_search(df, n_trials=n_trials)

    candidate_bundle, cand_m = walk_forward_train(df, params=params)
    # Prefer honest chronological holdout metrics already computed inside walk_forward_train
    hold = cand_m.get("holdout") or {}
    for k in ("expectancy_R", "max_dd", "brier", "n_signals", "auc", "hit_rate"):
        if k in hold:
            cand_m[k] = hold[k]
    cand_m["train_start"] = df.index.min().date()
    cand_m["train_end"] = df.index.max().date()
    cand_m["n_symbols"] = int(df["symbol"].nunique()) if "symbol" in df.columns else 0

    _, test_df = time_train_test_split(df, test_ratio=0.2, embargo=5)
    champ = registry.champion(name)
    champ_m = None
    if champ:
        champ_bundle = registry.load(champ)
        # Compare champions on the same holdout window using their stored decision path
        eval_df = test_df if len(test_df) >= 40 else df
        raw = evaluate_walk_forward(champ_bundle, eval_df, last_n_sessions=min(120, len(eval_df)))
        # If champion has nested holdout in metrics, prefer that for fairness on first load
        champ_m = champ.metrics.get("holdout") if isinstance(champ.metrics, dict) and champ.metrics.get("holdout") else raw

    cand_exp = float(cand_m.get("expectancy_R") or 0)
    champ_exp = float((champ_m or {}).get("expectancy_R") or 0)
    cand_hit = float(cand_m.get("hit_rate") or 0)
    champ_hit = float((champ_m or {}).get("hit_rate") or 0)

    # First model always promotes; later need holdout improvement
    promote = champ_m is None or (
        cand_exp >= champ_exp + 0.02
        and cand_m.get("max_dd", 999) <= champ_m.get("max_dd", 999) * 1.15 + 1e-9
        and cand_m.get("brier", 1) <= champ_m.get("brier", 1) + 0.02
        and cand_m.get("n_signals", 0) >= 10
    )
    # Always promote if current champion is degenerate (all probs collapsed)
    if champ and champ_m and champ_m.get("n_signals", 0) == 0:
        promote = True
    # Replace a losing champion whenever candidate is strictly less bad
    if champ_m is not None and champ_exp < 0 and cand_exp > champ_exp and cand_m.get("n_signals", 0) >= 10:
        promote = True
    # Or when hit-rate is catastrophic (<15%) and candidate improves it
    if champ_m is not None and champ_hit < 0.15 and cand_hit > champ_hit + 0.02 and cand_m.get("n_signals", 0) >= 10:
        promote = True
    # Never keep promoting deeper losses when champion was already non-negative
    if champ_m is not None and champ_exp >= 0 and cand_exp < -0.05:
        promote = False

    mv = registry.save(candidate_bundle, metrics=cand_m, name=name, status="candidate")
    if promote:
        registry.promote(mv)
        logger.info("Promoted %s_v%s metrics=%s", name, mv.version, cand_m)
    else:
        logger.info("Kept champion; candidate %s_v%s not better", name, mv.version)
    return mv, promote


def maybe_auto_rollback(name: str = "swing_lgbm") -> bool:
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
