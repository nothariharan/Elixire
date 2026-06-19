"""Verify all Elixire Band agents can connect."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ELIXIR_ROOT = ROOT.parent
sys.path.insert(0, str(ELIXIR_ROOT))

from band.adapters import LangGraphAdapter
from gateway.config import AGENT_NAMES, load_agent_credentials

from band_agents.shared.agent_factory import bootstrap_env, create_remote_agent, make_llm, memory_checkpointer

ROLES = ("receptionist", "intake", "brief", "gateway")


async def _check_role(role: str) -> tuple[str, bool, str]:
    creds = load_agent_credentials(role)
    if not creds:
        return role, False, "credentials missing — set BAND_* env vars or local agent_config.yaml"

    agent_dir = ROOT / role
    os.chdir(agent_dir)
    adapter = LangGraphAdapter(
        llm=make_llm(),
        checkpointer=memory_checkpointer(),
        custom_section=f"connectivity test for {role}",
    )
    agent = create_remote_agent(role, adapter, agent_dir)
    try:
        await agent.start()
        name = agent.agent_name or AGENT_NAMES[role]
        await agent.stop()
        return role, True, name
    except Exception as exc:
        return role, False, str(exc)


async def main() -> int:
    bootstrap_env()
    print("checking band agent connectivity…")
    failed = 0
    for role in ROLES:
        role_name, ok, detail = await _check_role(role)
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {role_name}: {detail}")
        if not ok:
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
