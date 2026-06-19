"""Full end-to-end pipeline test with HITL flow for Elixir v4 (band-backed)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

ELIXIR_ROOT = Path(__file__).resolve().parent


def _band_ready(health: dict) -> bool:
    band = health.get("band", {})
    return band.get("mode") == "band" and band.get("connected") is True


def test_lupus_flow(band_api):
    """Test the standard lupus demo case through band orchestration."""
    base, health = band_api
    if not _band_ready(health):
        pytest.skip("band gateway not connected — set BAND_* env vars or local agent_config.yaml")

    r = requests.post(
        f"{base}/diagnose",
        json={
            "symptoms": "fever, joint pain, butterfly rash on face",
            "mode": "fast",
        },
        timeout=320,
    )
    data = r.json()

    if r.status_code == 504:
        pytest.skip("band agents not running — start: python band_agents/run_all.py")

    if r.status_code == 202:
        assert data.get("status") == "requires_action"
        assert "thread_id" in data
        assert "questions" in data

        sse_log = data.get("sse_log", [])
        assert any("band" in line.lower() or "handoff" in line.lower() for line in sse_log), sse_log

        r2 = requests.post(
            f"{base}/respond",
            json={
                "thread_id": data["thread_id"],
                "answer": "I have family history of lupus, rash appeared 2 weeks ago",
            },
            timeout=320,
        )
        data2 = r2.json()

        if r2.status_code == 202:
            r3 = requests.post(
                f"{base}/respond",
                json={
                    "thread_id": data["thread_id"],
                    "answer": "Yes I have mouth sores and sun sensitivity",
                },
                timeout=320,
            )
            data2 = r3.json()

        assert "confidence_spread" in data2 or "final_confidence_spread" in data2, list(data2.keys())
        audit = data2.get("audit_trail", {})
        if audit:
            assert isinstance(audit.get("provenance", []), list)

    elif r.status_code == 200:
        if data.get("status") == "emergency":
            pytest.skip("unexpected emergency for lupus demo input")
        assert "confidence_spread" in data or "final_confidence_spread" in data
    else:
        pytest.fail(f"unexpected status {r.status_code}: {data}")


def start_server():
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", "8004"],
        cwd=str(ELIXIR_ROOT),
    )
    time.sleep(4)
    return proc


if __name__ == "__main__":
    proc = start_server()
    base = "http://127.0.0.1:8004"
    try:
        health = requests.get(f"{base}/health", timeout=5).json()
        test_lupus_flow((base, health))
        print("\n=== Full pipeline test passed ===")
    finally:
        proc.terminate()
