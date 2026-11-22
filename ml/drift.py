from __future__ import annotations

import numpy as np
import pandas as pd

from config import trading_config as cfg
from ml.models import DriftReport


def psi(expected: np.ndarray, actual: np.ndarray, buckets: int = 10) -> float:
    expected = expected[~np.isnan(expected)]
    actual = actual[~np.isnan(actual)]
    if len(expected) < 20 or len(actual) < 20:
        return 0.0
    quantiles = np.linspace(0, 1, buckets + 1)
    breaks = np.unique(np.quantile(expected, quantiles))
    if len(breaks) < 3:
        return 0.0
    e_counts = np.histogram(expected, bins=breaks)[0].astype(float)
    a_counts = np.histogram(actual, bins=breaks)[0].astype(float)
    e_perc = (e_counts + 1e-6) / (e_counts.sum() + 1e-6 * len(e_counts))
    a_perc = (a_counts + 1e-6) / (a_counts.sum() + 1e-6 * len(a_counts))
    return float(np.sum((a_perc - e_perc) * np.log(a_perc / e_perc)))


def check_drift(
    ref_features: pd.DataFrame,
    cur_features: pd.DataFrame,
    model_name: str = "swing_lgbm",
    top_cols: list[str] | None = None,
) -> DriftReport:
    cols = top_cols or list(ref_features.columns)[:10]
    details = {}
    max_psi = 0.0
    for col in cols:
        if col not in ref_features.columns or col not in cur_features.columns:
            continue
        val = psi(ref_features[col].values.astype(float), cur_features[col].values.astype(float))
        details[col] = val
        max_psi = max(max_psi, val)
    triggered = max_psi > cfg.PSI_THRESHOLD
    return DriftReport.objects.create(
        model_name=model_name,
        psi_max=max_psi,
        details=details,
        triggered_retrain=triggered,
    )
