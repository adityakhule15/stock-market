"""Local F&O probability — bias + regime + liquidity (no Gemini)."""

from signals.fno import fno_local_probability


def test_preferred_ce_beats_counter_bias():
    plan = {
        "pricing": "LIVE_INDSTOCKS",
        "affordable": True,
        "net_edge_ok": True,
        "live": {"live_volume": 2_000_000, "live_oi": 80_000},
    }
    ce = fno_local_probability(
        option_type="CE",
        bullish=True,
        spot=26000,
        strike=26500,
        atr=120,
        plan=plan,
        regime={"tag": "trend", "nifty_ret": 0.01},
    )
    pe = fno_local_probability(
        option_type="PE",
        bullish=True,
        spot=26000,
        strike=25500,
        atr=120,
        plan=plan,
        regime={"tag": "trend", "nifty_ret": 0.01},
    )
    assert ce > pe
    assert 0.35 <= pe <= 0.72
    assert 0.35 <= ce <= 0.72


def test_high_vol_and_deep_otm_penalized():
    deep = fno_local_probability(
        option_type="CE",
        bullish=True,
        spot=26000,
        strike=28000,
        atr=100,
        plan={"pricing": "ESTIMATE", "affordable": True, "live": {}},
        regime={"tag": "high_vol"},
    )
    assert deep <= 0.45
