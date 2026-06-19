"""elixire-receptionist band remote agent — first patient contact."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
ELIXIR_ROOT = AGENT_DIR.parent.parent
sys.path.insert(0, str(ELIXIR_ROOT))

from band_agents.shared.delivering_adapter import DeliveringLangGraphAdapter

from band_agents.shared.agent_factory import (
    bootstrap_env,
    create_remote_agent,
    load_role_prompt,
    make_llm,
    memory_checkpointer,
)
from band_agents.shared.tools import run_receptionist

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("elixire.receptionist")


async def main() -> None:
    os.chdir(AGENT_DIR)
    bootstrap_env()

    backoff = 30
    attempt = 0
    while True:
        attempt += 1
        try:
            adapter = DeliveringLangGraphAdapter(
                role="receptionist",
                llm=make_llm("FEATHERLESS_MODEL_TRIAGE", temperature=0.0),
                checkpointer=memory_checkpointer(),
                additional_tools=[run_receptionist],
                custom_section=load_role_prompt(AGENT_DIR),
            )
            agent = create_remote_agent("receptionist", adapter, AGENT_DIR)
            logger.info("starting Elixire-Receptionist (attempt %d)…", attempt)
            await agent.run()
            # agent.run() returned cleanly — Band sent a graceful disconnect.
            # Reconnect after backoff so Band's session TTL can expire.
            wait = backoff + random.uniform(0, 20)
            logger.warning("Receptionist disconnected gracefully (attempt %d) — reconnecting in %.0fs", attempt, wait)
            await asyncio.sleep(wait)
            backoff = min(backoff * 2, 120)
        except Exception as exc:
            msg = str(exc).lower()
            if "already_connected" in msg or "429" in msg or "rate" in msg:
                # 90s minimum for already_connected so the old Band session can expire.
                # Jitter avoids all 3 agents retrying at the exact same moment.
                wait = max(backoff, 90) + random.uniform(0, 30)
                logger.warning("Band rate-limit (attempt %d): %s — retrying in %.0fs", attempt, exc, wait)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 120)
            else:
                logger.error("Receptionist fatal error (attempt %d): %s", attempt, exc)
                raise


if __name__ == "__main__":
    asyncio.run(main())
