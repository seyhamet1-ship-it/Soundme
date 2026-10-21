"""Shared pytest fixtures for Phase 9 auth-aware tests."""
from __future__ import annotations

import os
import pytest

# Disable model warm-up globally in tests so /api/health returns quickly.
os.environ.setdefault("WARM_MODELS_AT_STARTUP", "0")
os.environ.setdefault("ACCESS_TOKENS", "soundme-test-token")

TEST_TOKEN = "soundme-test-token"
ALT_TOKEN = "soundme-alt-token"


@pytest.fixture(autouse=True)
def set_test_token(monkeypatch):
    """Ensure ACCESS_TOKENS env var includes the test token for every test."""
    existing = os.environ.get("ACCESS_TOKENS", "")
    tokens = {t.strip() for t in existing.split(",") if t.strip()}
    tokens.add(TEST_TOKEN)
    monkeypatch.setenv("ACCESS_TOKENS", ",".join(sorted(tokens)))
    monkeypatch.setenv("WARM_MODELS_AT_STARTUP", "0")


@pytest.fixture
def auth_headers():
    """Return Authorization headers for the primary test token."""
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def alt_auth_headers(monkeypatch):
    """Return Authorization headers for a secondary token (cross-isolation tests)."""
    existing = os.environ.get("ACCESS_TOKENS", TEST_TOKEN)
    tokens = {t.strip() for t in existing.split(",") if t.strip()}
    tokens.add(ALT_TOKEN)
    monkeypatch.setenv("ACCESS_TOKENS", ",".join(sorted(tokens)))
    return {"Authorization": f"Bearer {ALT_TOKEN}"}
