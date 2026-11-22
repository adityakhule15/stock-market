"""Advisor free-form AI chat — local fallback + no order placement."""

import pytest
from rest_framework.test import APIClient

from dashboard.ai_chat import chat, provider_status


@pytest.mark.django_db
def test_local_chat_refuses_place_order():
    out = chat("Please place order for NIFTY CALL now")
    assert out["ok"] is True
    assert "place" in out["bot"].lower() or "won't" in out["bot"].lower() or "can't" in out["bot"].lower()
    assert out["provider"] in {"policy", "local"}


@pytest.mark.django_db
def test_local_chat_answers_trade_today():
    out = chat("Should I trade today?")
    assert out["ok"] is True
    assert len(out["bot"]) > 20
    assert "wallet" in out
    assert provider_status()["active"] in {"local", "gemini", "deepseek"}


@pytest.mark.django_db
def test_ai_chat_api():
    api = APIClient()
    resp = api.post(
        "/api/advisor/ai-chat/",
        {"message": "When is best trade no trade?", "history": []},
        format="json",
    )
    assert resp.status_code == 200
    assert resp.data["ok"] is True
    assert resp.data["bot"]


@pytest.mark.django_db
def test_ai_status_api():
    api = APIClient()
    resp = api.get("/api/advisor/ai-status/")
    assert resp.status_code == 200
    assert "active" in resp.data
