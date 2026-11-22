"""Tomorrow purchase plan API smoke."""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

import pytest
from rest_framework.test import APIClient

from portfolio.models import PortfolioSettings


@pytest.mark.django_db
def test_tomorrow_plan_endpoint():
    ps = PortfolioSettings.get_solo()
    ps.cash = Decimal("2000")
    ps.equity = Decimal("2000")
    ps.life_status = "alive"
    ps.fno_watchlist = ["NIFTY"]
    ps.save()
    api = APIClient()
    with mock.patch("dashboard.tomorrow_plan._scan_fresh", return_value=[]), mock.patch(
        "dashboard.tomorrow_plan._ai_coach",
        return_value={"provider": "local", "text": "VERDICT: SKIP_TOMORROW\n- wait", "error": None},
    ):
        resp = api.post("/api/advisor/tomorrow/", {"rescan": False}, format="json")
    assert resp.status_code == 200, resp.content
    assert resp.data["ok"] is True
    assert "session" in resp.data
    assert "wallet" in resp.data
    assert "picks" in resp.data
    assert resp.data["verdict"] in {"BUY_TOMORROW", "SKIP_TOMORROW"}
