"""INDstocks token expiry / auth cooldown — no live API calls."""

from __future__ import annotations

import base64
import json
import time
from unittest import mock

import marketdata.indstocks as ind


def _fake_jwt(*, exp: float) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(exp), "iat": int(exp) - 3600}).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def test_token_expired_from_jwt_claim(monkeypatch):
    tok = _fake_jwt(exp=time.time() - 60)
    monkeypatch.setenv("INDSTOCKS_ACCESS_TOKEN", tok)
    assert ind.token_expired(tok) is True
    assert ind.data_enabled() is False


def test_token_fresh_enables_data(monkeypatch):
    tok = _fake_jwt(exp=time.time() + 3600)
    monkeypatch.setenv("INDSTOCKS_ACCESS_TOKEN", tok)
    # Reset cooldown state
    ind._auth_fail_until = 0.0
    ind._auth_fail_token = ""
    ind._auth_warned = False
    assert ind.token_expired(tok) is False
    assert ind.data_enabled() is True


def test_403_marks_cooldown(monkeypatch):
    tok = _fake_jwt(exp=time.time() + 3600)
    monkeypatch.setenv("INDSTOCKS_ACCESS_TOKEN", tok)
    ind._auth_fail_until = 0.0
    ind._auth_fail_token = ""
    ind._auth_warned = False

    class FakeResp:
        status_code = 403
        text = '{"message":"expired","success":false}'

        def json(self):
            return {"message": "expired", "success": False}

        def raise_for_status(self):
            raise AssertionError("should not raise_for_status after 403 handler")

    with mock.patch("marketdata.indstocks.requests.get", return_value=FakeResp()):
        try:
            ind._get("/market/instruments/expiries", {"underlying": "NIFTY", "segment": "DERIVATIVE"})
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "403" in str(exc) or "auth" in str(exc).lower()
    assert ind.data_enabled() is False
