from signals.skill_checklist import (
    SKILL_IDS,
    build_skill_checklist,
    vendored_skills_present,
)


def test_all_eleven_skills_vendored():
    missing = vendored_skills_present()
    assert missing == [], f"missing SKILL.md: {missing}"
    assert len(SKILL_IDS) == 11


def test_checklist_has_eleven_rows():
    pipe = {
        "scanner": {"ok": True},
        "news": {"action": "ok", "reason": "clear"},
        "entry": {"ok": True, "status": "WAITING"},
        "exit": {"ok": True},
        "time_machine": {"verdict": "EDGE"},
        "budget": {"ok": True},
        "gate": {"result": "PASS", "ok": True},
        "ticket": {"text": "BUY RELIANCE"},
        "alerts": {"ok": True, "channels": ["telegram"]},
        "analyst": {"ok": True, "facts_only": True},
        "insider": {"ok": True, "flag": "none"},
    }
    rows = build_skill_checklist(pipe, gate_passed=True)
    assert len(rows) == 11
    assert {r["skill"] for r in rows} == set(SKILL_IDS)
    assert all(r["status"] in {"OK", "WARN", "BLOCK", "N-A"} for r in rows)


def test_risk_block_marks_trader_risk_block():
    rows = build_skill_checklist(
        {"gate": {"result": "BLOCK", "blocked_by": ["max loss"]}},
        gate_passed=False,
    )
    risk = next(r for r in rows if r["skill"] == "trader-risk")
    assert risk["status"] == "BLOCK"
