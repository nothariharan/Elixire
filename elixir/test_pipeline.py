"""Basic pipeline tests for Elixir v4."""
from __future__ import annotations

import requests


def test_health(legacy_api):
    r = requests.get(f"{legacy_api}/health", timeout=5)
    data = r.json()
    assert data["status"] == "ok"
    assert data["version"] in ("3.0-legacy", "4.0-band")
    assert "band" in data
    assert "agents" in data
    assert "llm" in data
    assert "FEATHERLESS_API_KEY" in data["env"]
    assert "AML_API_KEY" in data["env"]


def test_non_medical_rejection(legacy_api):
    r = requests.post(
        f"{legacy_api}/diagnose",
        json={"symptoms": "hello world"},
        timeout=30,
    )
    data = r.json()
    assert r.status_code == 400 or data.get("is_valid") is False or data.get("error")
    assert data.get("invalid") or data.get("error")
