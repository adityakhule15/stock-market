"""Shared pytest fixtures for StockScope UI / API smoke tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _allow_hosts(settings):
    hosts = list(settings.ALLOWED_HOSTS)
    for h in ("testserver", "localhost", "127.0.0.1"):
        if h not in hosts:
            hosts.append(h)
    settings.ALLOWED_HOSTS = hosts
