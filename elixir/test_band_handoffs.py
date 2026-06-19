"""assert band handoff chain appears in message content patterns."""
from __future__ import annotations

from gateway.mappers import detect_handoff, extract_json_payload, is_complete_message, is_hitl_message


def test_detect_handoff_intake_to_clinical():
    assert detect_handoff("@Elixir-Clinical please review") == "Intake → Clinical"


def test_detect_handoff_clinical_to_action():
    assert detect_handoff("handoff to @Elixir-Action") == "Clinical → Action"


def test_extract_json_payload_from_block():
    content = 'here is data\n```json\n{"elixir_status": "complete", "verified": true}\n```'
    data = extract_json_payload(content)
    assert data["elixir_status"] == "complete"


def test_hitl_detection():
    content = '```json\n{"elixir_status": "hitl", "follow_up_questions": ["how long?"]}\n```'
    assert is_hitl_message(content, "Elixir-Intake") is True


def test_complete_detection():
    content = '```json\n{"elixir_status": "complete", "action_plan_draft": "{}"}\n```'
    assert is_complete_message(content, "Elixir-Action") is True
