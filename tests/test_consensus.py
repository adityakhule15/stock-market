"""Consensus filter-mode invariants — pure functions."""

from signals.consensus import apply_consensus_filter, combine_votes, vote_tradingagents


def test_combine_renormalizes_over_available():
    out = combine_votes(
        {"lightgbm": 1.0, "kronos": None, "time_machine": 0.5, "tradingagents": None, "meta": None, "insider": None},
        weights={"lightgbm": 0.3, "kronos": 0.2, "time_machine": 0.2, "tradingagents": 0.15, "meta": 0.1, "insider": 0.05},
    )
    assert out["n_models"] == 2
    assert abs(sum(out["weights_used"].values()) - 1.0) < 1e-6
    assert out["score"] is not None
    assert out["thin"] is True  # < 3 models


def test_block_stays_block():
    applied = apply_consensus_filter(
        gate_passed=False,
        gate_reasons=["max loss"],
        consensus={"score": 1.0, "n_models": 5, "agree_k": 5, "thin": False},
    )
    assert applied["passed"] is False
    assert applied["status"] == "skipped_already_blocked"


def test_consensus_display_only_until_evidence(monkeypatch):
    """With <100 resolved shadow trades, veto stays off (display-only)."""
    applied = apply_consensus_filter(
        gate_passed=True,
        gate_reasons=[],
        consensus={"score": -0.5, "n_models": 4, "agree_k": 0, "thin": False},
    )
    assert applied["passed"] is True
    assert applied["consensus"].get("vetoed") is False


def test_consensus_veto_on_strong_negative(monkeypatch):
    class _QS:
        def filter(self, *a, **k):
            return self

        def exclude(self, *a, **k):
            return self

        def count(self):
            return 120

    class _ST:
        objects = _QS()

    monkeypatch.setattr("evidence.models.ShadowTrade", _ST, raising=False)
    # Patch the import site inside apply_consensus_filter
    import evidence.models as em

    monkeypatch.setattr(em, "ShadowTrade", _ST)
    applied = apply_consensus_filter(
        gate_passed=True,
        gate_reasons=[],
        consensus={"score": -0.5, "n_models": 4, "agree_k": 0, "thin": False},
    )
    assert applied["passed"] is False
    assert "consensus_veto" in applied["reasons"]
    assert applied["consensus"]["vetoed"] is True


def test_thin_consensus_never_vetoes():
    applied = apply_consensus_filter(
        gate_passed=True,
        gate_reasons=[],
        consensus={"score": -0.9, "n_models": 2, "agree_k": 0, "thin": True},
    )
    assert applied["passed"] is True
    assert applied["consensus"]["vetoed"] is False


def test_agree_badge():
    applied = apply_consensus_filter(
        gate_passed=True,
        gate_reasons=[],
        consensus={"score": 0.4, "n_models": 4, "agree_k": 3, "thin": False},
    )
    assert applied["passed"] is True
    assert applied["status"] == "agree"
    assert "models_agree" in (applied["consensus"]["badge"] or "")


def test_tradingagents_vote_mapping():
    assert vote_tradingagents("Buy") == 1.0
    assert vote_tradingagents("Overweight") == 0.5
    assert vote_tradingagents("Hold") == 0.0
    assert vote_tradingagents("Underweight") == -0.5
    assert vote_tradingagents("Sell") == -1.0
    assert vote_tradingagents("REVIEW") is None
