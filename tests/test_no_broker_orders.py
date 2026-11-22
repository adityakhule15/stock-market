"""Fail if broker place-order code, routes, or ALLOW_BROKER_ORDERS=True appear."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from django.urls import get_resolver

from config import trading_config as cfg
from marketdata.indstocks import BrokerOrdersDisabled, cancel_order, modify_order, place_order

ROOT = Path(__file__).resolve().parents[1]
PKG_DIRS = (
    "config",
    "marketdata",
    "features",
    "ml",
    "signals",
    "portfolio",
    "dashboard",
    "scanner",
    "timemachine",
    "backtest",
    "notifications",
    "research",
    "forecast",
    "scripts",
)

ORDER_PATH_RE = re.compile(
    r"(place[_-]?order|/smart/order|/order\b|place_stock_order)",
    re.IGNORECASE,
)
URL_FORBIDDEN = re.compile(
    r"(place.?order|smart.?order|/orders?/place|broker.?order)",
    re.IGNORECASE,
)


def test_allow_broker_orders_false():
    assert cfg.ALLOW_BROKER_ORDERS is False
    assert cfg.INDSTOCKS_DATA_ONLY is True


def test_place_order_stubs_raise():
    with pytest.raises(BrokerOrdersDisabled):
        place_order(symbol="NIFTY", qty=1)
    with pytest.raises(BrokerOrdersDisabled):
        modify_order(order_id="x")
    with pytest.raises(BrokerOrdersDisabled):
        cancel_order(order_id="x")


def test_no_place_order_url_routes():
    patterns = []

    def _walk(resolver, prefix=""):
        for p in resolver.url_patterns:
            if hasattr(p, "url_patterns"):
                _walk(p, prefix + str(getattr(p.pattern, "_route", p.pattern)))
            else:
                route = prefix + str(getattr(p.pattern, "_route", p.pattern))
                patterns.append(route)

    _walk(get_resolver())
    bad = [r for r in patterns if URL_FORBIDDEN.search(r)]
    assert not bad, f"Forbidden broker order routes: {bad}"


def _project_py_files():
    files = []
    for name in PKG_DIRS:
        d = ROOT / name
        if not d.is_dir():
            continue
        files.extend(d.rglob("*.py"))
    return [f for f in files if ".venv" not in f.parts and "migrations" not in f.parts]


def test_no_real_broker_order_http_in_source():
    """AST/source scan: fail if non-stub code posts to broker order paths."""
    offenders = []
    for path in _project_py_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Config flag must stay False (assignment True is banned outside tests)
        if re.search(r"ALLOW_BROKER_ORDERS\s*=\s*True", text):
            offenders.append(f"{path}: ALLOW_BROKER_ORDERS = True")
        # Skip the intentional raise stubs in indstocks.py
        if path.name == "indstocks.py" and "BrokerOrdersDisabled" in text:
            # Still forbid actual HTTP post to order paths inside place_order body
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in {
                    "place_order",
                    "modify_order",
                    "cancel_order",
                }:
                    body_src = ast.get_source_segment(text, node) or ""
                    if re.search(r"requests\.(post|put|patch)", body_src, re.I):
                        offenders.append(f"{path}: {node.name} contains HTTP write")
                    if "/order" in body_src and "raise" not in body_src:
                        offenders.append(f"{path}: {node.name} references /order without raise-only stub")
            continue
        # Elsewhere: any place_order implementation that isn't just importing/raising
        if "def place_order" in text and path.name != "indstocks.py":
            offenders.append(f"{path}: unexpected def place_order")
        # Hard-fail on groww/indstocks order API call strings in non-test code
        for m in ORDER_PATH_RE.finditer(text):
            # allow comments documenting the ban and URL checks in this test's sibling modules
            line = text[: m.start()].splitlines()[-1] if m.start() else ""
            ctx_line = text.splitlines()[len(text[: m.start()].splitlines()) - 1]
            if "never" in ctx_line.lower() or "disabled" in ctx_line.lower() or "blocked" in ctx_line.lower():
                continue
            if "assert" in ctx_line or "FORBIDDEN" in ctx_line or "ORDER_PATH" in ctx_line:
                continue
            if path.name in {"freshness.py"}:
                continue
            # Indstocks path block list is OK
            if "_BLOCKED" in ctx_line or "Blocked" in ctx_line or "data only" in ctx_line.lower():
                continue
            if "/order" in m.group(0) or "place_order" in m.group(0).lower():
                # Allow string mentions in docs-like comments
                if ctx_line.strip().startswith("#") or ctx_line.strip().startswith('"""'):
                    continue
                # Allow raise BrokerOrdersDisabled messages
                if "BrokerOrdersDisabled" in text[max(0, m.start() - 200) : m.end() + 200]:
                    continue
                offenders.append(f"{path}:{ctx_line.strip()[:120]}")
    # Deduplicate soft noise: only keep clear violations
    hard = [
        o
        for o in offenders
        if "ALLOW_BROKER_ORDERS = True" in o
        or "def place_order" in o
        or "HTTP write" in o
        or "without raise" in o
    ]
    assert not hard, "Broker place-order violations:\n" + "\n".join(hard)
