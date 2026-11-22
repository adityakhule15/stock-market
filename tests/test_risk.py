from config.trading_config import FEES
from signals.risk import estimate_costs, position_size, round_tick


def test_round_tick_directions():
    assert round_tick(101.02, "up") == 101.05
    assert round_tick(101.02, "down") == 101.00
    assert round_tick(101.02, "nearest") == 101.00


def test_position_size_caps():
    # risk ₹40, risk/share ₹3 -> 13 by risk; capital 50% of 2000 / 100 = 10 by cap
    assert position_size(100, 97, 2000, 0.02, 0.5) == 10


def test_position_size_zero_risk():
    assert position_size(100, 100, 2000, 0.02, 0.5) == 0


def test_estimate_costs_positive():
    c = estimate_costs(900, 1000, "CNC", FEES)
    assert c > 0
