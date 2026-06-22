import os

import pytest


@pytest.fixture(autouse=True)
def _fake_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-pytest")
