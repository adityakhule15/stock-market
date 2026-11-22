from unittest.mock import MagicMock, patch

from signals.analyst import analyst_for_symbol


def test_analyst_never_votes_and_reports_facts():
    import pandas as pd

    # Columns = quarters (latest first), rows = line items
    stmt = pd.DataFrame(
        {
            "2026-06-30": [1000.0, 100.0, 5.0, 10.0],
            "2026-03-31": [900.0, 90.0, 4.5, 8.0],
            "2025-12-31": [880.0, 85.0, 4.0, 7.0],
            "2025-09-30": [860.0, 80.0, 3.8, 6.0],
            "2025-06-30": [800.0, 70.0, 3.5, 5.0],
        },
        index=["Total Revenue", "Net Income", "Basic EPS", "Other Income Expense"],
    )
    fake_ticker = MagicMock()
    fake_ticker.quarterly_income_stmt = stmt
    fake_ticker.quarterly_financials = stmt

    with (
        patch("yfinance.Ticker", return_value=fake_ticker),
        patch("marketdata.events.next_results_date", return_value=None),
    ):
        # Clear cache between runs by unique as_of
        from datetime import date

        out = analyst_for_symbol("RELIANCE", as_of=date(2026, 11, 20))
    assert out["ok"] is True
    assert out["vote"] is None
    assert out["facts_only"] is True
    assert out["revenue"]["yoy"] is not None
    assert "summary" in out


def test_analyst_fail_open():
    with patch("yfinance.Ticker", side_effect=RuntimeError("boom")):
        from datetime import date

        out = analyst_for_symbol("TCS", as_of=date(2026, 11, 21))
    assert out["unavailable"] is True
    assert out["vote"] is None
