"""Smoke tests for NIFTY/BANKNIFTY Patterns Desk."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from django.test import Client


def _ohlcv(n: int = 200, start: float = 22000.0) -> pd.DataFrame:
    rng = np.random.default_rng(19)
    idx = pd.date_range("2024-01-01", periods=n, freq="B", tz="Asia/Kolkata")
    rets = rng.normal(0, 0.008, size=n)
    close = start * np.cumprod(1 + rets)
    open_ = np.roll(close, 1)
    open_[0] = start
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.004, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.004, n))
    vol = rng.integers(1000, 5000, size=n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


@pytest.mark.django_db
def test_pattern_detectors_smoke():
    from indexdesk.patterns import bias_from_patterns, detect_index_patterns

    daily = _ohlcv(180)
    # Fake 5m session
    m5 = _ohlcv(40, start=float(daily["close"].iloc[-1]))
    m5.index = pd.date_range("2024-09-20 09:15", periods=40, freq="5min", tz="Asia/Kolkata")
    hits = detect_index_patterns(daily=daily, bars_5m=m5, spot=float(m5["close"].iloc[-1]))
    assert isinstance(hits, list)
    blend = bias_from_patterns(hits, model_prob_up=0.61)
    assert blend["bias"] in {"BULLISH", "BEARISH", "NEUTRAL"}
    assert 5 <= blend["prob_up_pct"] <= 95


@pytest.mark.django_db
def test_build_index_dataset_and_train_matrix():
    from indexdesk.features import INDEX_FEATURE_COLS, build_index_dataset, feature_matrix_index

    ds = build_index_dataset(_ohlcv(220), horizon=3)
    assert not ds.empty
    assert "y" in ds.columns
    X, y = feature_matrix_index(ds)
    assert len(X) == len(y)
    assert y.nunique() >= 1
    for c in INDEX_FEATURE_COLS[:5]:
        assert c in X.columns


@pytest.mark.django_db
def test_patterns_page_renders():
    c = Client()
    r = c.get("/patterns/")
    assert r.status_code == 200
    assert b"Patterns" in r.content
    assert b"NIFTY" in r.content


@pytest.mark.django_db
def test_patterns_board_api_with_mocks():
    from indexdesk.models import IndexPatternSnapshot

    for name, spot in (("NIFTY", 24500.0), ("BANKNIFTY", 52000.0)):
        IndexPatternSnapshot.objects.create(
            underlying=name,
            session_date=date.today(),
            spot=spot,
            spot_source="test",
            patterns=[{"name": "ema_stack_bull", "side": "BULLISH", "confidence": 0.62}],
            model_prob_up=0.58,
            bias="BULLISH",
            call={"action": "SKIP", "reason": "test"},
            position_advice={"open": [], "summary": "none"},
            meta={"blend": {"prob_up_pct": 58.0, "prob_down_pct": 42.0}},
        )
    c = Client()
    with patch("indexdesk.engine.analyze_underlying") as mocked:
        mocked.side_effect = AssertionError("should use fresh snapshot")
        r = c.get("/api/patterns/board/")
    assert r.status_code == 200
    body = r.json()
    assert body.get("ok") is True
    assert "NIFTY" in body.get("board", {})
    assert "BANKNIFTY" in body.get("board", {})


@pytest.mark.django_db
def test_coach_context_includes_patterns():
    from indexdesk.models import IndexPatternSnapshot
    from indexdesk.coach_context import patterns_context_blob

    IndexPatternSnapshot.objects.create(
        underlying="BANKNIFTY",
        session_date=date.today(),
        spot=52000.0,
        spot_source="test",
        patterns=[],
        bias="NEUTRAL",
        call={"action": "SKIP"},
        position_advice={"open": []},
        meta={"blend": {"prob_up_pct": 50.0, "prob_down_pct": 50.0}},
    )
    blob = patterns_context_blob(refresh_if_stale=False)
    assert "Patterns Desk" in blob
    assert "BANKNIFTY" in blob


@pytest.mark.django_db
def test_patterns_page_has_backfill_and_train_buttons():
    c = Client()
    r = c.get("/patterns/")
    assert r.status_code == 200
    assert b'id="btn-patterns-backfill"' in r.content
    assert b'id="btn-patterns-train"' in r.content


@pytest.mark.django_db
def test_patterns_train_api_with_mock():
    from rest_framework.test import APIClient

    class _MV:
        version = 9

    with patch("indexdesk.train.train_index_model", return_value=(_MV(), True, {"expectancy_R": 0.5, "auc": 0.55, "n_rows": 100})):
        api = APIClient()
        resp = api.post("/api/patterns/train/", {"promote": True}, format="json")
    assert resp.status_code == 200
    assert resp.data["ok"] is True
    assert resp.data["version"] == 9
    assert resp.data["promoted"] is True


@pytest.mark.django_db
def test_ai_chat_context_mentions_patterns_desk():
    from dashboard.ai_chat import _context_blob
    from indexdesk.models import IndexPatternSnapshot

    IndexPatternSnapshot.objects.create(
        underlying="NIFTY",
        session_date=date.today(),
        spot=24000.0,
        bias="BULLISH",
        call={"action": "SKIP"},
        meta={"blend": {"prob_up_pct": 60.0, "prob_down_pct": 40.0}},
    )
    blob = _context_blob()
    assert "Patterns Desk" in blob
