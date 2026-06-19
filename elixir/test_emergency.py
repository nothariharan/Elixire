"""Emergency gate tests — zero LLM calls required."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

ELIXIR_ROOT = Path(__file__).resolve().parent


def test_cardiac_emergency(legacy_api):
    r = requests.post(
        f"{legacy_api}/diagnose",
        json={"symptoms": "crushing chest pain and can't breathe"},
        timeout=30,
    )
    data = r.json()
    assert data.get("status") == "emergency", f"Expected emergency: {data}"
    assert data.get("action") == "seek_immediate_care"
    log = data.get("model_provider_log", [])
    assert len(log) == 0, f"Expected no LLM calls but got: {log}"


def test_crisis_detection(legacy_api):
    r = requests.post(
        f"{legacy_api}/diagnose",
        json={"symptoms": "I have pain and I want to end my life"},
        timeout=30,
    )
    data = r.json()
    assert data.get("status") == "emergency", f"Expected emergency: {data}"
    assert data.get("action") == "crisis_line"
    assert "helplines" in data, "Expected crisis helplines"


def test_non_emergency_passes():
    """Unit test — no LLM; confirms benign input does not trigger emergency gate."""
    from nodes.emergency import emergency_node

    state = emergency_node(
        {"raw_input": "mild headache and fatigue for 3 days", "sse_log": []}
    )
    assert not state.get("emergency_flag"), f"False positive emergency: {state}"


if __name__ == "__main__":
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", "8005"],
        cwd=str(ELIXIR_ROOT),
        env={**__import__("os").environ, "ELIXIR_LEGACY_GRAPH": "1"},
    )
    base = "http://127.0.0.1:8005"
    try:
        for _ in range(30):
            try:
                requests.get(f"{base}/health", timeout=2)
                break
            except requests.RequestException:
                time.sleep(1)

        test_cardiac_emergency(base)
        test_crisis_detection(base)
        test_non_emergency_passes(base)
        print("\n=== All emergency tests passed ===")
    finally:
        proc.terminate()
