"""shared helpers for band remote agents."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Iterator, List, Optional

from band import Agent

from dotenv import load_dotenv
from gateway.config import REST_URL, WS_URL, load_agent_credentials
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.callbacks import CallbackManagerForLLMRun
from langgraph.checkpoint.memory import InMemorySaver

ELIXIR_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = Path(__file__).resolve().parent.parent


def bootstrap_env() -> None:
    if str(ELIXIR_ROOT) not in sys.path:
        sys.path.insert(0, str(ELIXIR_ROOT))
    load_dotenv(ELIXIR_ROOT / ".env")


def create_remote_agent(
    role: str,
    adapter,
    agent_dir: Path,
    *,
    ws_url: str = WS_URL,
    rest_url: str = REST_URL,
) -> Agent:
    """Create a Band remote agent from env vars or local agent_config.yaml."""
    creds = load_agent_credentials(role)
    if not creds:
        cfg_path = agent_dir / "agent_config.yaml"
        raise RuntimeError(
            f"Band {role} not configured — set BAND_{role.upper()}_API_KEY and "
            f"BAND_{role.upper()}_AGENT_ID env vars, or create {cfg_path} locally"
        )
    return Agent.create(
        adapter=adapter,
        agent_id=creds.agent_id,
        api_key=creds.api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )


def load_role_prompt(agent_dir: Path) -> str:
    path = agent_dir / "prompts" / "role.md"
    return path.read_text(encoding="utf-8")


def _sanitize_for_bedrock(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Bedrock requires conversations to start with a user/human message.

    When Band replays backlog from old rooms, history may start with an
    assistant turn. Drop leading non-human messages (keep system messages
    in front) so Bedrock never rejects with ValidationException.
    """
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    non_system  = [m for m in messages if not isinstance(m, SystemMessage)]
    if not non_system:
        return messages
    if not isinstance(non_system[0], HumanMessage):
        first_human = next((i for i, m in enumerate(non_system) if isinstance(m, HumanMessage)), None)
        if first_human is not None:
            non_system = non_system[first_human:]
        else:
            # No human messages at all — prepend a minimal one so Bedrock accepts the call
            non_system = [HumanMessage(content="Please continue.")] + non_system
    return system_msgs + non_system


def make_llm(model_env: str = "FEATHERLESS_MODEL_TRIAGE", temperature: float = 0.0):
    """Return LangChain chat model for the active LLM_PROVIDER."""
    provider = os.getenv("LLM_PROVIDER", "featherless").lower()
    if provider == "bedrock":
        from langchain_aws import ChatBedrockConverse

        class _SafeBedrockConverse(ChatBedrockConverse):
            """Sanitizes messages before sending so Bedrock never sees a non-user first turn."""

            def _stream(
                self,
                messages: List[BaseMessage],
                stop: Optional[List[str]] = None,
                run_manager: Optional[CallbackManagerForLLMRun] = None,
                **kwargs: Any,
            ) -> Iterator[ChatGenerationChunk]:
                return super()._stream(_sanitize_for_bedrock(messages), stop=stop, run_manager=run_manager, **kwargs)

            async def _astream(
                self,
                messages: List[BaseMessage],
                stop: Optional[List[str]] = None,
                run_manager: Optional[CallbackManagerForLLMRun] = None,
                **kwargs: Any,
            ):
                async for chunk in super()._astream(_sanitize_for_bedrock(messages), stop=stop, run_manager=run_manager, **kwargs):
                    yield chunk

            def _generate(
                self,
                messages: List[BaseMessage],
                stop: Optional[List[str]] = None,
                run_manager: Optional[CallbackManagerForLLMRun] = None,
                **kwargs: Any,
            ):
                return super()._generate(_sanitize_for_bedrock(messages), stop=stop, run_manager=run_manager, **kwargs)

        return _SafeBedrockConverse(
            model_id=os.getenv("ORCHESTRATOR_MODEL_ID", "amazon.nova-lite-v1:0"),
            region_name=os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")),
            temperature=temperature,
        )
    return ChatOpenAI(
        base_url="https://api.featherless.ai/v1",
        api_key=os.getenv("FEATHERLESS_API_KEY", ""),
        model=os.getenv(model_env, "Qwen/Qwen2.5-72B-Instruct"),
        temperature=temperature,
    )


def make_featherless_llm(model_env: str = "FEATHERLESS_MODEL_TRIAGE", temperature: float = 0.0) -> ChatOpenAI:
    """Legacy alias — prefer make_llm() which respects LLM_PROVIDER."""
    return make_llm(model_env, temperature)


def memory_checkpointer() -> InMemorySaver:
    return InMemorySaver()
