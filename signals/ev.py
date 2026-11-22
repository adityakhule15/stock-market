"""Expected rupees after charges — gate + ranking helper."""

from __future__ import annotations

from typing import Any

from config import trading_config as cfg


def expected_rupees(
    *,
    probability: float,
    win_rs: float,
    loss_rs: float,
    charges_rs: float = 0.0,
) -> dict[str, Any]:
    """EV = p * win_net - (1-p) * loss_net.

    win_rs / loss_rs should already be gross; we subtract charges once if not
    already net. Prefer passing net win/loss from trade_economics.
    """
    p = max(0.0, min(1.0, float(probability)))
    win = float(win_rs)
    loss = abs(float(loss_rs))
    # If callers pass gross, charges_rs can haircut; if already net, pass charges=0
    win_net = win - float(charges_rs) if charges_rs and win > 0 else win
    # loss already typically includes adverse move; charges on losing round-trip
    # are embedded when using economics at stop — keep loss as given
    ev = p * win_net - (1.0 - p) * loss
    return {
        "ev_rs": round(ev, 2),
        "p": round(p, 4),
        "win_net": round(win_net, 2),
        "loss_rs": round(loss, 2),
        "ok": ev >= float(getattr(cfg, "EV_MIN_RS", 5.0)),
        "min_rs": float(getattr(cfg, "EV_MIN_RS", 5.0)),
    }


def ev_from_order(order: dict, probability: float | None = None) -> dict[str, Any]:
    p = float(probability if probability is not None else order.get("probability") or 0.5)
    econ = order.get("economics") if isinstance(order.get("economics"), dict) else {}
    win = float(econ.get("net_pnl") or order.get("net_pnl") or 0)
    loss = float(order.get("max_loss") or 0)
    return expected_rupees(probability=p, win_rs=win, loss_rs=loss, charges_rs=0.0)


def rank_key_ev(signal) -> float:
    """Higher is better: expected Rs per Rs at risk."""
    meta = getattr(signal, "rank_meta", None) or {}
    if meta.get("ev_per_risk") is not None:
        return float(meta["ev_per_risk"])
    ev = float(meta.get("ev_rs") or 0)
    risk = float(getattr(signal, "max_loss", 0) or 1)
    return ev / max(risk, 1.0)
