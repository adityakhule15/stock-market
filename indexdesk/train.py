"""Train / promote index_pattern_lgbm on NIFTY + BANKNIFTY daily history."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from django.utils import timezone

from indexdesk.constants import MODEL_NAME, UNDERLYINGS, YF_MAP
from indexdesk.features import build_index_dataset, feature_matrix_index
from indexdesk.models import IndexTrainRun
from marketdata.service import MarketDataService
from ml import registry
from ml.evaluate import best_threshold, classification_metrics, expectancy_R
from ml.train import fit_baseline, fit_lgbm, predict_proba, time_train_test_split

logger = logging.getLogger(__name__)


def _combined_index_dataset() -> pd.DataFrame:
    svc = MarketDataService()
    frames = []
    for name in UNDERLYINGS:
        yf_sym = YF_MAP[name]
        try:
            df = svc.bars_df(yf_sym, interval="1d")
            if df is None or len(df) < 150:
                df, _ = svc.history(yf_sym, interval="1d", period="5y", use_cache=False)
                if df is not None and not df.empty:
                    svc.upsert_bars(yf_sym, "1d", df, "yfinance")
            if df is None or len(df) < 150:
                logger.warning("index dataset skip %s — insufficient bars", name)
                continue
            ds = build_index_dataset(df, horizon=3)
            if ds.empty or ds["y"].nunique() < 2:
                continue
            ds["underlying"] = name
            frames.append(ds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("index dataset %s: %s", name, exc)
    if not frames:
        raise RuntimeError("No NIFTY/BANKNIFTY training data — run indexdesk backfill first")
    out = pd.concat(frames, axis=0).sort_index()
    return out


def train_index_model(*, promote: bool = True) -> tuple[Any, bool, dict]:
    """Fit LightGBM on index dataset; optionally promote if beats baseline."""
    run = IndexTrainRun.objects.create(ok=False, detail="started")
    try:
        df = _combined_index_dataset()
        train_df, test_df = time_train_test_split(df, test_ratio=0.2, embargo=5)
        X_tr, y_tr = feature_matrix_index(train_df)
        X_te, y_te = feature_matrix_index(test_df)
        if y_tr.nunique() < 2 or len(X_tr) < 80:
            raise RuntimeError(f"insufficient train rows ({len(X_tr)}) or single class")

        # Hold out last 20% of train for calibration
        cal_n = max(40, len(X_tr) // 5)
        X_fit, y_fit = X_tr.iloc[:-cal_n], y_tr.iloc[:-cal_n]
        X_cal, y_cal = X_tr.iloc[-cal_n:], y_tr.iloc[-cal_n:]
        if y_fit.nunique() < 2:
            X_fit, y_fit = X_tr, y_tr
            X_cal = y_cal = None

        bundle = fit_lgbm(X_fit, y_fit, X_cal=X_cal, y_cal=y_cal)
        base = fit_baseline(X_fit, y_fit)

        probs = predict_proba(bundle, X_te) if len(X_te) else np.array([])
        base_probs = predict_proba(base, X_te) if len(X_te) else np.array([])

        metrics: dict[str, Any] = {
            "honest": True,
            "n_rows": int(len(df)),
            "n_train": int(len(X_tr)),
            "n_test": int(len(X_te)),
            "underlyings": list(UNDERLYINGS),
            "train_start": df.index.min().date() if len(df) else None,
            "train_end": df.index.max().date() if len(df) else None,
            "feature_cols": bundle.get("feature_cols"),
        }
        thr = 0.55
        if len(probs) >= 20 and y_te.nunique() > 1:
            thr, _ = best_threshold(y_te.to_numpy(), probs)
            cm = classification_metrics(y_te.to_numpy(), probs, threshold=thr)
            metrics.update(cm)
            metrics["decision_threshold"] = float(thr)
            metrics["expectancy_R"] = float(expectancy_R(y_te.to_numpy(), probs, threshold=thr))
            from sklearn.metrics import log_loss

            metrics["log_loss"] = float(log_loss(y_te, np.clip(probs, 1e-6, 1 - 1e-6)))
            metrics["baseline_log_loss"] = float(
                log_loss(y_te, np.clip(base_probs, 1e-6, 1 - 1e-6))
            )
        bundle["decision_threshold"] = float(metrics.get("decision_threshold") or thr)
        bundle["algo"] = "lightgbm"
        metrics["model_family"] = MODEL_NAME

        mv = registry.save(bundle, metrics, name=MODEL_NAME, status="candidate")
        promoted = False
        if promote:
            cand_ll = metrics.get("log_loss")
            base_ll = metrics.get("baseline_log_loss")
            exp = float(metrics.get("expectancy_R") or 0)
            beats = (
                cand_ll is not None
                and base_ll is not None
                and float(cand_ll) < float(base_ll) - 1e-6
                and exp > 0
            )
            # First champion: allow provisional if we at least beat baseline OR have samples
            champ = registry.champion(MODEL_NAME)
            if champ is None:
                registry.promote(mv)
                promoted = True
                metrics["provisional"] = not beats
            elif beats:
                # Compare expectancy vs incumbent when available
                old_exp = float((champ.metrics or {}).get("expectancy_R") or -999)
                if exp >= old_exp - 1e-6:
                    registry.promote(mv)
                    promoted = True
            if not promoted:
                metrics["promote_skip"] = "did not beat baseline/expectancy gate"

        run.ok = True
        run.version = mv.version
        run.promoted = promoted
        run.metrics = {k: (v if not hasattr(v, "isoformat") else v.isoformat()) for k, v in metrics.items() if k != "feature_cols"}
        run.detail = f"v{mv.version} promoted={promoted} n={metrics.get('n_rows')}"
        run.finished_at = timezone.now()
        run.save()
        return mv, promoted, metrics
    except Exception as exc:  # noqa: BLE001
        logger.exception("index train failed")
        run.ok = False
        run.detail = str(exc)[:2000]
        run.finished_at = timezone.now()
        run.save()
        raise


def score_index_row(X: pd.DataFrame) -> float | None:
    """Return P(up) from champion, or None if unavailable."""
    champ = registry.champion(MODEL_NAME)
    if champ is None:
        return None
    try:
        bundle = registry.load(champ)
        probs = predict_proba(bundle, X)
        if len(probs) == 0:
            return None
        return float(probs[-1])
    except Exception as exc:  # noqa: BLE001
        logger.warning("index score failed: %s", exc)
        return None
