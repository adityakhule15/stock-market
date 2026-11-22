"""Groww ticket — human-facing fields to type in Groww. Never executes."""

from __future__ import annotations

from typing import Any


def build_groww_ticket(
    *,
    symbol: str,
    side: str,
    product: str,
    entry_trigger: float,
    entry_limit: float,
    sl_trigger: float,
    target1: float,
    qty: int,
    lots: int = 0,
    option_type: str | None = None,
    strike: float | None = None,
    expiry: str | None = None,
    square_off_by: str | None = None,
    checklist_complete: bool = False,
) -> dict[str, Any]:
    bare = symbol.replace(".NS", "").replace(".BO", "")
    fields = {
        "symbol": bare,
        "side": side,
        "product": product,
        "quantity": qty if lots <= 0 else lots,
        "qty_label": f"{lots} lot(s)" if lots > 0 else f"{qty} shares",
        "order_type": "LIMIT",
        "limit_price": round(float(entry_limit), 2),
        "trigger_price": round(float(entry_trigger), 2),
        "stop_loss": round(float(sl_trigger), 2),
        "target": round(float(target1), 2),
    }
    if option_type:
        fields["option_type"] = option_type
    if strike is not None:
        fields["strike"] = strike
    if expiry:
        fields["expiry"] = expiry
    if square_off_by:
        fields["square_off_by"] = square_off_by

    copy_lines = [
        f"Symbol: {fields['symbol']}",
        f"Side: {side}",
        f"Product: {product}",
        f"Qty: {fields['qty_label']}",
        f"Trigger: {fields['trigger_price']}",
        f"Limit: {fields['limit_price']}",
        f"SL: {fields['stop_loss']}",
        f"Target: {fields['target']}",
    ]
    if option_type and strike is not None:
        copy_lines.insert(1, f"Contract: {bare} {strike} {option_type}" + (f" {expiry}" if expiry else ""))
    if square_off_by:
        copy_lines.append(f"Square off by: {square_off_by} IST")

    return {
        "ok": True,
        "broker": "groww",
        "execution": "manual_only",
        "fields": fields,
        "copy_text": "\n".join(copy_lines),
        "copy_enabled": bool(checklist_complete),
        "checklist_required": True,
        "reason": "Type these fields in Groww yourself — StockScope never places orders",
    }


CHECKLIST_ITEMS = (
    {"id": "sl_placed", "label": "I will place SL in Groww before/with entry"},
    {"id": "size_matches", "label": "Quantity/lots matches this ticket"},
    {"id": "not_chasing", "label": "I am not chasing — trigger has hit (or I accept waiting)"},
)
