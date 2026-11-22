"""Retrain / meta-filter must not promote below min samples."""

import pytest

from config import trading_config as cfg
from ml.meta_filter import maybe_promote_meta, train_meta_candidate


@pytest.mark.django_db
def test_meta_filter_refuses_below_min_samples():
    out = train_meta_candidate(min_n=cfg.RETRAIN_MIN_SAMPLES)
    assert out.get("promoted") is False
    assert out.get("n", 0) < cfg.RETRAIN_MIN_SAMPLES or "need" in out.get("reason", "").lower() or out.get("n") == 0


@pytest.mark.django_db
def test_maybe_promote_respects_min_n():
    out = maybe_promote_meta(min_n=10_000)
    assert out.get("promoted") is False
