"""verify elixir-intake connects to band."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
ELIXIR_ROOT = AGENT_DIR.parent.parent
sys.path.insert(0, str(ELIXIR_ROOT))

from band.adapters import LangGraphAdapter

from band_agents.shared.agent_factory import (
    bootstrap_env,
    create_remote_agent,
    make_llm,
    memory_checkpointer,
)


async def main() -> None:
    os.chdir(AGENT_DIR)
    bootstrap_env()

    adapter = LangGraphAdapter(
        llm=make_llm(),
        checkpointer=memory_checkpointer(),
        custom_section="connectivity test agent",
    )
    agent = create_remote_agent("receptionist", adapter, AGENT_DIR)
    await agent.start()
    print(f"connected: {agent.agent_name}")
    await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
