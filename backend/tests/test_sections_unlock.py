"""Test JWT tokenu pre odomknutie sekcií."""

from __future__ import annotations

import os

import pytest

from app.services.sections_unlock import (
    issue_sections_unlock_token,
    verify_sections_unlock_token,
)


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTHUB_AUTH_SECRET", "test-secret-min-16-chars")


def test_sections_unlock_token_roundtrip() -> None:
    token, exp = issue_sections_unlock_token(42)
    assert exp > 0
    assert verify_sections_unlock_token(token, 42)
    assert not verify_sections_unlock_token(token, 99)
    assert not verify_sections_unlock_token("", 42)


def test_sections_unlock_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTHUB_AUTH_SECRET", "")
    with pytest.raises(RuntimeError):
        issue_sections_unlock_token(1)
    assert not verify_sections_unlock_token("bad", 1)
