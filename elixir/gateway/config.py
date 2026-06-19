"""load band agent credentials and platform urls."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ELIXIR_ROOT = Path(__file__).resolve().parent.parent
BAND_AGENTS_ROOT = ELIXIR_ROOT / "band_agents"

REST_URL = os.getenv("THENVOI_REST_URL", "https://app.band.ai/").rstrip("/")
WS_URL = os.getenv("THENVOI_WS_URL", "wss://app.band.ai/api/v1/socket/websocket")

AGENT_NAMES = {
    "receptionist": "Elixire-Intake",
    "intake": "Elixire-Clinical",
    "brief": "Elixire-Action",
    "gateway": "Elixire-Gateway",
}

AGENT_HANDLES = {
    "receptionist": "nothariharan/elixir-intake",
    "intake": "nothariharan/elixir-clinical",
    "brief": "nothariharan/elixir-action",
    "gateway": "nothariharan/elixir-gateway",
}

ROLE_ENV_KEYS = {
    "gateway": ("BAND_GATEWAY_API_KEY", "BAND_GATEWAY_AGENT_ID"),
    "receptionist": ("BAND_RECEPTIONIST_API_KEY", "BAND_RECEPTIONIST_AGENT_ID"),
    "intake": ("BAND_INTAKE_API_KEY", "BAND_INTAKE_AGENT_ID"),
    "brief": ("BAND_BRIEF_API_KEY", "BAND_BRIEF_AGENT_ID"),
}


@dataclass
class AgentCredentials:
    role: str
    agent_id: str
    api_key: str
    name: str
    handle: str


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _credentials_from_env(role: str) -> tuple[str, str] | None:
    api_key_var, agent_id_var = ROLE_ENV_KEYS[role]
    api_key = os.getenv(api_key_var, "").strip()
    agent_id = os.getenv(agent_id_var, "").strip()
    if api_key and agent_id:
        return agent_id, api_key
    return None


def _credentials_from_yaml(role: str) -> tuple[str, str] | None:
    cfg_path = BAND_AGENTS_ROOT / role / "agent_config.yaml"
    data = _load_yaml(cfg_path)
    entry = data.get("my_agent") or data
    agent_id = (entry.get("agent_id") or "").strip()
    api_key = (entry.get("api_key") or "").strip()
    if agent_id and api_key:
        return agent_id, api_key
    return None


def load_agent_credentials(role: str) -> AgentCredentials | None:
    """Load credentials: env vars first, then local agent_config.yaml (dev only)."""
    pair = _credentials_from_env(role) or _credentials_from_yaml(role)
    if not pair:
        return None
    agent_id, api_key = pair
    return AgentCredentials(
        role=role,
        agent_id=agent_id,
        api_key=api_key,
        name=AGENT_NAMES[role],
        handle=AGENT_HANDLES[role],
    )


def agent_config_path(role: str) -> Path:
    """Local dev fallback path — must stay gitignored."""
    return BAND_AGENTS_ROOT / role / "agent_config.yaml"


def all_agents_configured() -> bool:
    return all(load_agent_credentials(r) for r in ("receptionist", "intake", "brief", "gateway"))
