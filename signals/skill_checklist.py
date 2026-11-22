"""Day-trader skill parity checklist (11 sub-skills) for the call panel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SKILL_IDS = (
    "trader-scanner",
    "trader-news",
    "trader-entry",
    "trader-exit",
    "trader-timemachine",
    "trader-risk",
    "trader-budget",
    "trader-broker",
    "trader-alerts",
    "trader-analyst",
    "trader-pelosi",  # India: SEBI PIT insider tracker
)

# Map skill id → pipeline key (StockScope names)
SKILL_TO_PIPELINE = {
    "trader-scanner": "scanner",
    "trader-news": "news",
    "trader-entry": "entry",
    "trader-exit": "exit",
    "trader-timemachine": "time_machine",
    "trader-risk": "gate",
    "trader-budget": "budget",
    "trader-broker": "ticket",
    "trader-alerts": "alerts",
    "trader-analyst": "analyst",
    "trader-pelosi": "insider",
}


def vendored_skill_root() -> Path:
    return Path(__file__).resolve().parents[1] / "third_party" / "day-trader-skill"


def vendored_skills_present() -> list[str]:
    root = vendored_skill_root()
    missing = []
    for sid in SKILL_IDS:
        if not (root / sid / "SKILL.md").exists():
            missing.append(sid)
    return missing


def build_skill_checklist(pipeline: dict[str, Any], *, gate_passed: bool | None = None) -> list[dict[str, Any]]:
    """Return 11 rows with status OK / WARN / BLOCK / N-A."""
    pipe = pipeline or {}
    rows: list[dict[str, Any]] = []

    def status_for(skill: str) -> tuple[str, str]:
        key = SKILL_TO_PIPELINE[skill]
        stage = pipe.get(key) or {}

        if skill == "trader-broker":
            # Ticket copy only — never live orders
            if stage.get("text") or stage.get("groww") or stage.get("ticket"):
                return "OK", "Groww ticket copy only (no broker orders)"
            return "WARN", "ticket not built"

        if skill == "trader-alerts":
            if stage.get("sent") or stage.get("ok") or stage.get("channels"):
                return "OK", "alerts stage present"
            return "N-A", "no alert sent yet"

        if skill == "trader-analyst":
            if stage.get("unavailable"):
                return "WARN", stage.get("reason") or "analyst unavailable"
            if stage.get("ok"):
                note = "facts only — no vote"
                if stage.get("results_inside_trade"):
                    return "WARN", f"{note}; results inside trade"
                return "OK", note
            return "WARN", "analyst not run"

        if skill == "trader-pelosi":
            if stage.get("unavailable"):
                return "WARN", stage.get("reason") or "insider unavailable"
            flag = stage.get("flag") or "none"
            if flag == "pledge":
                return "WARN", stage.get("label") or "pledge"
            if flag in {"buy", "sell"}:
                return "OK", stage.get("label") or flag
            if stage.get("ok"):
                return "N-A", "no insider activity"
            return "WARN", "insider not run"

        if skill == "trader-risk":
            if gate_passed is False or (isinstance(stage, dict) and stage.get("result") == "BLOCK"):
                return "BLOCK", ",".join(stage.get("blocked_by") or stage.get("reasons") or [])[:120] or "blocked"
            if gate_passed is True or (isinstance(stage, dict) and stage.get("result") == "PASS"):
                return "OK", "risk gate PASS"
            return "WARN", "gate pending"

        if skill == "trader-timemachine":
            v = stage.get("verdict") or (pipe.get("time_machine") or {}).get("verdict")
            if v == "COIN_FLIP":
                return "BLOCK", "COIN_FLIP"
            if v == "EDGE":
                return "OK", "EDGE"
            if v:
                return "WARN", str(v)
            return "WARN", "time machine missing"

        if skill == "trader-news":
            if stage.get("block"):
                return "BLOCK", stage.get("reason") or "news block"
            if stage.get("caution"):
                return "WARN", stage.get("reason") or "news caution"
            return "OK", stage.get("reason") or "clear"

        # Generic stages
        if not stage:
            return "WARN", f"{key} empty"
        if stage.get("ok") is False:
            return "WARN", stage.get("reason") or "not ok"
        return "OK", stage.get("reason") or stage.get("setup") or "ok"

    for sid in SKILL_IDS:
        st, detail = status_for(sid)
        rows.append(
            {
                "skill": sid,
                "pipeline_key": SKILL_TO_PIPELINE[sid],
                "status": st,
                "detail": detail,
            }
        )
    return rows
