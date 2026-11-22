"""Mocked Kronos glue — no torch / no HF downloads in CI."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest


def test_resolve_kronos_python_never_django(monkeypatch, tmp_path):
    import sys
    from pathlib import Path

    from forecast import adapter

    monkeypatch.delenv("KRONOS_PYTHON", raising=False)
    monkeypatch.setattr(adapter, "project_path", lambda *parts: tmp_path.joinpath(*parts))
    got = adapter.resolve_kronos_python()
    assert Path(got).resolve() != Path(sys.executable).resolve()
    assert adapter.kronos_sidecar_installed() is False


def test_next_session_timestamps_daily_skips_weekend():
    from forecast.adapter import next_session_timestamps

    # Friday close → next stamps are Mon..
    fr = "2026-11-20T15:30:00+05:30"
    stamps = next_session_timestamps(fr, 3, interval="1d")
    assert len(stamps) == 3
    days = [pd.Timestamp(s).day_name() for s in stamps]
    assert "Saturday" not in days
    assert "Sunday" not in days


@pytest.mark.django_db
def test_forecast_symbol_mocked_ok(settings):
    from forecast.models import KronosForecast
    from forecast.services import forecast_symbol

    bars = pd.DataFrame(
        {
            "open": [100 + i for i in range(80)],
            "high": [101 + i for i in range(80)],
            "low": [99 + i for i in range(80)],
            "close": [100.5 + i for i in range(80)],
            "volume": [1e6] * 80,
        },
        index=pd.date_range("2026-01-01", periods=80, freq="B", tz="Asia/Kolkata"),
    )
    fake_paths = [
        [
            {
                "ts": "2026-04-01T15:30:00+05:30",
                "open": 200,
                "high": 210,
                "low": 198,
                "close": 205,
                "volume": 1,
                "amount": 1,
            }
        ]
        for _ in range(8)
    ]
    fake = {
        "ok": True,
        "paths": fake_paths,
        "paths_n": 8,
        "lookback": 80,
        "model_id": "NeoQuasar/Kronos-small",
        "last_close": float(bars["close"].iloc[-1]),
    }
    with (
        patch("forecast.services.kronos_enabled", return_value=True),
        patch("forecast.services.kronos_sidecar_installed", return_value=True),
        patch("forecast.services.invoke_kronos", return_value=fake),
    ):
        run = forecast_symbol(
            "RELIANCE",
            as_of=date(2026, 11, 20),
            bars_df=bars,
            entry=180.0,
            stop=170.0,
            target=220.0,
            force=True,
        )
    assert run.status == KronosForecast.Status.OK
    assert run.call in {"BUY", "NEUTRAL", "AVOID"}
    assert run.stats.get("p_up") is not None


@pytest.mark.django_db
def test_forecast_fail_open_when_disabled():
    from forecast.models import KronosForecast
    from forecast.services import forecast_symbol

    with patch("forecast.services.kronos_enabled", return_value=False):
        run = forecast_symbol("TCS", as_of=date(2026, 11, 20), force=True)
    assert run.status == KronosForecast.Status.ERROR
    assert "disabled" in (run.error or "").lower()
