"""unit tests for band shared tools."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ELIXIR_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ELIXIR_ROOT))

from band_agents.shared.tools import validate_input, run_triage  # noqa: E402


def test_validate_input_accepts_medical_symptoms():
    out = json.loads(validate_input.invoke({"raw_input": "fever and joint pain"}))
    assert out["is_valid"] is True


def test_validate_input_rejects_non_medical():
    out = json.loads(validate_input.invoke({"raw_input": "hello world foo bar"}))
    assert out["is_valid"] is False


@pytest.mark.skipif(
    not (
        __import__("os").getenv("FEATHERLESS_API_KEY")
        or (
            __import__("os").getenv("LLM_PROVIDER", "").lower() == "bedrock"
            and __import__("os").getenv("AWS_ACCESS_KEY_ID")
        )
    ),
    reason="LLM credentials required for live triage test",
)
def test_run_triage_returns_expected_keys():
    case = json.dumps({
        "raw_input": "fever, joint pain, butterfly rash",
        "mode": "fast",
        "locale": "en",
        "patient_history_timeline": [],
        "patient_responses": [],
        "follow_up_count": 0,
    })
    out = json.loads(run_triage.invoke({"case_json": case}))
    assert "standardized_symptoms" in out
    assert "elixir_status" in out
