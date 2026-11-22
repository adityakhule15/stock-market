"""Pure Kronos path stats — no torch."""

from forecast.stats import path_outcomes, vote_from_stats


def _path(closes, highs=None, lows=None):
    highs = highs or closes
    lows = lows or closes
    return [
        {
            "ts": f"2026-01-0{i+1}",
            "open": c,
            "high": highs[i],
            "low": lows[i],
            "close": c,
            "volume": 1.0,
        }
        for i, c in enumerate(closes)
    ]


def test_p_up_and_call_buy():
    paths = [_path([101, 102, 103])] * 10
    stats = path_outcomes(paths, entry=100.0, stop=95.0, target=110.0, side="BUY")
    assert stats["p_up"] == 1.0
    assert stats["paths_n"] == 10
    assert stats["call"] in {"BUY", "NEUTRAL"}


def test_same_bar_target_and_stop_counts_as_stop():
    # high above target and low below stop in same bar
    paths = [_path([100], highs=[120], lows=[80])]
    stats = path_outcomes(paths, entry=100.0, stop=90.0, target=110.0, side="BUY")
    assert stats["p_stop_first"] == 1.0
    assert stats["p_target_first"] == 0.0


def test_target_first():
    paths = [_path([100, 105], highs=[100, 115], lows=[99, 104])]
    stats = path_outcomes(paths, entry=100.0, stop=90.0, target=110.0, side="BUY")
    assert stats["p_target_first"] == 1.0


def test_vote_from_stats():
    v = vote_from_stats({"p_up": 0.8, "p_target_first": 0.7, "p_stop_first": 0.2})
    assert v is not None and v > 0
    assert vote_from_stats(None) is None
    assert vote_from_stats({}) is None
