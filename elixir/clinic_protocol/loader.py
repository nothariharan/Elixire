"""Load and save clinic protocols from JSON files."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from clinic_protocol.schema import ClinicProtocol

_DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"
_SAVED_DIR = Path(__file__).resolve().parent / "saved"


def _ensure_saved_dir() -> None:
    _SAVED_DIR.mkdir(exist_ok=True)


def load_protocol(clinic_id: str) -> ClinicProtocol | None:
    """Load a saved clinic protocol by ID, or fall back to a default specialty."""
    _ensure_saved_dir()
    saved_path = _SAVED_DIR / f"{clinic_id}.json"
    if saved_path.exists():
        data = json.loads(saved_path.read_text(encoding="utf-8"))
        return ClinicProtocol(**data)
    # Try loading a named default (e.g. clinic_id == "ophthalmology")
    default_path = _DEFAULTS_DIR / f"{clinic_id}.json"
    if default_path.exists():
        data = json.loads(default_path.read_text(encoding="utf-8"))
        return ClinicProtocol(**data)
    return None


def save_protocol(protocol: ClinicProtocol) -> str:
    """Save a clinic protocol; returns the clinic_id."""
    _ensure_saved_dir()
    if not protocol.clinic_id or protocol.clinic_id == "new":
        protocol = protocol.model_copy(update={"clinic_id": str(uuid.uuid4())})
    out_path = _SAVED_DIR / f"{protocol.clinic_id}.json"
    out_path.write_text(protocol.model_dump_json(indent=2), encoding="utf-8")
    return protocol.clinic_id


def list_protocols() -> list[str]:
    """Return all saved clinic_ids."""
    _ensure_saved_dir()
    return [p.stem for p in _SAVED_DIR.glob("*.json")]


def load_default(specialty: str) -> ClinicProtocol | None:
    """Load a bundled default protocol for a specialty."""
    path = _DEFAULTS_DIR / f"{specialty}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return ClinicProtocol(**data)
