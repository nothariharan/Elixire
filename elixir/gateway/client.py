"""rest client wrapper for elixir-gateway."""
from __future__ import annotations

import json
import logging

from band.client.rest import AsyncRestClient, DEFAULT_REQUEST_OPTIONS
from thenvoi_rest import ChatMessageRequest, ChatMessageRequestMentionsItem, ChatRoomRequest, ParticipantRequest

from gateway.config import AgentCredentials, REST_URL

logger = logging.getLogger("elixir.gateway.client")
REQ = DEFAULT_REQUEST_OPTIONS


class BandClient:
    def __init__(self, creds: AgentCredentials):
        self.creds = creds
        self._client = AsyncRestClient(api_key=creds.api_key, base_url=REST_URL)

    async def me(self):
        return await self._client.agent_api_identity.get_agent_me(request_options=REQ)

    async def create_chat(self, task_id: str | None = None) -> str:
        resp = await self._client.agent_api_chats.create_agent_chat(
            chat=ChatRoomRequest(task_id=task_id or None),
            request_options=REQ,
        )
        return resp.data.id

    async def add_participant(self, chat_id: str, participant_id: str) -> None:
        await self._client.agent_api_participants.add_agent_chat_participant(
            chat_id,
            participant=ParticipantRequest(participant_id=participant_id),
            request_options=REQ,
        )

    async def send_message(self, chat_id: str, content: str, mention: AgentCredentials) -> None:
        await self._client.agent_api_messages.create_agent_chat_message(
            chat_id,
            message=ChatMessageRequest(
                content=content,
                mentions=[
                    ChatMessageRequestMentionsItem(
                        id=mention.agent_id,
                        name=mention.name,
                        handle=mention.handle,
                    )
                ],
            ),
            request_options=REQ,
        )

    async def get_context(self, chat_id: str):
        return await self._client.agent_api_context.get_agent_chat_context(
            chat_id, page_size=100, request_options=REQ
        )

    async def close(self) -> None:
        wrapper = getattr(self._client, "_client_wrapper", None)
        if wrapper is None:
            return
        http = getattr(wrapper, "httpx_client", None)
        if http is None:
            return
        client = getattr(http, "httpx_client", http)
        aclose = getattr(client, "aclose", None)
        if callable(aclose):
            await aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()


_ENRICHED_KEYS = (
    "patient_name", "patient_dob", "patient_contact",
    "appointment_type", "clinic_id", "clinic_protocol",
    "locale", "uploaded_documents",
)


def case_payload(
    raw_input: str,
    mode: str,
    locale: str,
    patient_history: list,
    patient_responses: list | None = None,
    follow_up_count: int = 0,
) -> dict:
    payload: dict = {
        "raw_input": raw_input,
        "mode": mode,
        "locale": locale,
        "patient_history_timeline": patient_history,
        "patient_responses": patient_responses or [],
        "follow_up_count": follow_up_count,
    }

    # /intake sends a JSON blob as raw_input that contains clinic_protocol,
    # patient identity, and other context. Unpack it into top-level keys so
    # that _base_elixire_state() in tools.py can read them correctly.
    try:
        enriched = json.loads(raw_input) if isinstance(raw_input, str) else {}
        if isinstance(enriched, dict) and "clinic_protocol" in enriched:
            for k in _ENRICHED_KEYS:
                if k in enriched:
                    payload[k] = enriched[k]
            # Replace raw_input with just the patient's message, not the whole blob
            patient_message = enriched.get("message", "").strip()
            payload["raw_input"] = patient_message or raw_input
    except (json.JSONDecodeError, TypeError):
        pass

    return payload


def format_case_message(target: AgentCredentials, payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"@{target.handle} please process this elixir case:\n```json\n{body}\n```"
