"""Band collaboration tests — mapper unit tests + live handoff integration."""
from __future__ import annotations

import pytest
import requests

from gateway.client import case_payload
from gateway.mappers import (
    build_result_from_payload,
    detect_handoff,
    extract_json_payload,
    is_complete_message,
)


def test_case_payload_structure():
    payload = case_payload(
        raw_input="fever and rash",
        mode="fast",
        locale="en",
        patient_history=[{"date": "2024-01", "event_type": "symptom", "description": "rash"}],
        patient_responses=["yes"],
        follow_up_count=1,
    )
    for key in (
        "raw_input",
        "mode",
        "locale",
        "patient_history_timeline",
        "patient_responses",
        "follow_up_count",
    ):
        assert key in payload


def test_handoff_chain_detection():
    assert detect_handoff("@Elixir-Clinical please review") == "Intake → Clinical"
    assert detect_handoff("handoff to @Elixir-Action") == "Clinical → Action"
    assert detect_handoff('{"elixir_status": "complete", "action_plan_draft": "{}"}') == "Action → Human"


def test_build_result_from_complete_payload():
    payload = {
        "elixir_status": "complete",
        "action_plan_draft": '{"summary": "test"}',
        "final_confidence_spread": [{"condition": "Lupus", "probability": 0.5}],
        "verified": True,
        "provenance": [],
        "model_provider_log": [],
    }
    result = build_result_from_payload(payload, "thread-1", 100)
    assert result["status"] == "complete"
    assert result["thread_id"] == "thread-1"
    assert result["verified"] is True


def test_extract_json_from_handoff_block():
    content = (
        "handoff to clinical\n"
        '```json\n{"elixir_status": "handoff", "target": "Elixir-Clinical", "raw_input": "fever"}\n```'
    )
    data = extract_json_payload(content)
    assert data["elixir_status"] == "handoff"
    assert data["target"] == "Elixir-Clinical"


def test_complete_message_from_action_agent():
    content = '```json\n{"elixir_status": "complete", "action_plan_draft": "{}"}\n```'
    assert is_complete_message(content, "Elixir-Action") is True


def _band_ready(health: dict) -> bool:
    band = health.get("band", {})
    return band.get("mode") == "band" and band.get("connected") is True


def _assert_handoff_chain(sse_log: list[str]) -> None:
    joined = "\n".join(sse_log).lower()
    assert "band room created" in joined, sse_log
    assert "gateway → intake" in joined or "gateway -> intake" in joined, sse_log
    assert "intake → clinical" in joined or "intake -> clinical" in joined, sse_log
    assert "clinical → action" in joined or "clinical -> action" in joined, sse_log


@pytest.mark.band
@pytest.mark.slow
@pytest.mark.integration
def test_band_handoff_chain(band_api):
    """Requires BAND_* env vars or local agent_config.yaml + `python band_agents/run_all.py`."""
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

    sse_log = data.get("sse_log", [])
    assert sse_log, "expected sse_log from band orchestration"

    if r.status_code == 202:
        assert data.get("status") == "requires_action"
        _assert_handoff_chain(sse_log)
        return

    if r.status_code == 200 and data.get("status") != "emergency":
        _assert_handoff_chain(sse_log)
        return

    pytest.fail(f"unexpected response {r.status_code}: {data}")
