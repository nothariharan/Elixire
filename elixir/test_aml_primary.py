"""AML primary path test — requires valid AML_API_KEY in environment."""
from __future__ import annotations

import os

import pytest
import requests


def _aml_configured() -> bool:
    key = os.getenv("AML_API_KEY", "")
    model = os.getenv("AML_MODEL_VERIFICATION", "")
    return bool(key) and not key.startswith("YOUR_") and bool(model)


@pytest.mark.slow
@pytest.mark.integration
def test_aml_primary(legacy_api):
    if not _aml_configured():
        pytest.skip("AML_API_KEY and AML_MODEL_VERIFICATION required for primary path test")

    r = requests.post(
        f"{legacy_api}/diagnose",
        json={
            "symptoms": "fever, joint pain, butterfly rash on face",
            "mode": "fast",
        },
        timeout=320,
    )
    data = r.json()

    while r.status_code == 202:
        r = requests.post(
            f"{legacy_api}/respond",
            json={
                "thread_id": data["thread_id"],
                "answer": "family history of lupus, rash for 2 weeks",
            },
            timeout=320,
        )
        data = r.json()

    if data.get("status") == "emergency":
        pytest.skip("got emergency response — cannot test AML path")

    audit = data.get("audit_trail", {})
    provider_log = audit.get("model_provider_log", data.get("model_provider_log", []))
    verification_entries = [p for p in provider_log if p.get("node") == "verification"]

    assert verification_entries, "expected verification entry in model_provider_log"
    entry = verification_entries[0]
    assert entry.get("provider") == "aml", f"Expected aml provider, got: {entry}"
    assert entry.get("verified_by") == "aml", f"Expected verified_by=aml, got: {entry}"
