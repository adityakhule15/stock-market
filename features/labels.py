from __future__ import annotations

import pandas as pd

from backtest.simulator import label_series


def triple_barrier(
    df: pd.DataFrame,
    atr_mult_sl: float = 1.5,
    rr: float = 2.0,
    horizon: int = 5,
) -> pd.Series:
    """Label = did the *ticket* hit target before stop (entry must trigger).

    Uses the shared simulator so training matches live tickets (breakout entry,
    ATR stop, T1_R target, stop-first, optional adverse exit). ``rr`` is kept
    for call-site compatibility; target distance comes from ``T1_R`` in config
    via the simulator (typically 2.0R).
    """
    # Map legacy rr arg ≈ T1_R when callers pass it
    return label_series(
        df,
        atr_mult_sl=atr_mult_sl,
        t1_r=float(rr),
        horizon=horizon,
        apply_management=True,
    )
