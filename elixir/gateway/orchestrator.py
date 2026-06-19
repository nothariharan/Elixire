"""band chat-room orchestration for elixir-gateway — REST polling only."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from gateway.client import BandClient, case_payload, format_case_message
from gateway.config import load_agent_credentials
from gateway.mappers import (
    build_hitl_response,
    build_result_from_payload,
    detect_handoff,
    extract_json_payload,
    is_complete_message,
    is_hitl_message,
    message_to_sse,
)
from gateway.session import create_session, get_session, update_session

logger = logging.getLogger("elixire.gateway.orchestrator")


def _msg_key(msg) -> str:
    """Stable dedup key for a context message.
    Band context items often have id=None, so fall back to sender+content prefix."""
    mid = getattr(msg, "id", None)
    if mid:
        return str(mid)
    content = (getattr(msg, "content", "") or "")[:200]
    sender = getattr(msg, "sender_name", "") or ""
    return f"{sender}::{content}"


def _handoff_timeout() -> int:
    return int(os.getenv("BAND_HANDOFF_TIMEOUT", "90"))


_POLL_INTERVAL = 2.0   # seconds between REST context polls


class BandOrchestrator:
    async def run_case(
        self,
        thread_id: str,
        raw_input: str,
        mode: str,
        locale: str,
        patient_history: list,
        patient_responses: list | None = None,
        follow_up_count: int = 0,
    ) -> tuple[dict[str, Any], int]:
        gateway = load_agent_credentials("gateway")
        receptionist = load_agent_credentials("receptionist")
        intake = load_agent_credentials("intake")
        brief = load_agent_credentials("brief")
        if not all([gateway, receptionist, intake, brief]):
            raise RuntimeError("band agents not configured — set BAND_* env vars or local agent_config.yaml")

        start = time.time()
        sse_log: list[str] = ["[system] band room created"]

        payload = case_payload(
            raw_input, mode, locale, patient_history, patient_responses, follow_up_count
        )

        async with BandClient(gateway) as client:
            room_id = await client.create_chat()
            sse_log.append(f"[system] room {room_id[:8]}…")

            for creds in (receptionist, intake, brief):
                await client.add_participant(room_id, creds.agent_id)
                sse_log.append(f"[system] added {creds.name}")

            create_session(
                thread_id=thread_id,
                room_id=room_id,
                raw_input=raw_input,
                mode=mode,
                locale=locale,
                patient_history=patient_history,
                follow_up_count=follow_up_count,
                patient_responses=patient_responses or [],
                sse_log=sse_log,
            )

            msg = format_case_message(receptionist, payload)
            await client.send_message(room_id, msg, receptionist)
            sse_log.append("[handoff] Gateway → Receptionist")

            status, body = await self._poll_until_done(
                client, room_id, thread_id, sse_log, start,
                self_name=gateway.name,
            )

            latency = int((time.time() - start) * 1000)
            body["latency_ms"] = latency
            body["sse_log"] = sse_log + body.get("sse_log", [])
            update_session(thread_id, status=status, sse_log=body["sse_log"])
            return body, 202 if status == "hitl" else 200

    async def resume_case(self, thread_id: str, answer: str) -> tuple[dict[str, Any], int]:
        session = get_session(thread_id)
        if not session:
            raise KeyError(f"unknown thread_id: {thread_id}")

        gateway = load_agent_credentials("gateway")
        intake = load_agent_credentials("intake")
        if not gateway or not intake:
            raise RuntimeError("band gateway/intake not configured — set BAND_* env vars or local agent_config.yaml")

        session.follow_up_count += 1
        session.patient_responses = session.patient_responses + [answer]
        session.sse_log.append("[system] patient response received")

        start = time.time()

        payload = case_payload(
            session.raw_input,
            session.mode,
            session.locale,
            session.patient_history,
            session.patient_responses,
            session.follow_up_count,
        )
        # Merge prior intake state so the node can pair Q&A and carry forward chief_complaint etc.
        # NOTE: follow_up_questions is deliberately excluded — the intake node pairs Q&A via
        # asked_questions, and carrying follow_up_questions would make the gateway's own outgoing
        # message look like a HITL reply (is_hitl_message matches any payload with that key),
        # causing the gateway to echo the stale question back to the patient.
        if session.last_intake_payload:
            carry = {
                k: v for k, v in session.last_intake_payload.items()
                if k not in ("elixire_status", "sse_log", "model_provider_log",
                             "error", "patient_responses", "follow_up_count",
                             "follow_up_questions")
            }
            payload.update(carry)

        async with BandClient(gateway) as client:
            # Only accept messages added after this snapshot (avoids stale HITL replay).
            context_min_index = 0
            prior_ids: set[str] = set()
            try:
                ctx = await client.get_context(session.room_id)
                context_min_index = len(ctx.data)
                for m in ctx.data:
                    prior_ids.add(_msg_key(m))
                logger.debug(
                    "resume_case: snapshotted %d messages, min_index=%d",
                    len(prior_ids), context_min_index,
                )
            except Exception as exc:
                logger.warning("resume_case: snapshot failed (%s) — min_index=0", exc)

            msg = format_case_message(intake, payload)
            await client.send_message(session.room_id, msg, intake)
            session.sse_log.append(
                f"[handoff] Gateway → Intake (follow-up #{session.follow_up_count})"
            )

            status, body = await self._poll_until_done(
                client, session.room_id, thread_id, session.sse_log, start,
                prior_ids=prior_ids,
                context_min_index=context_min_index,
                self_name=gateway.name,
            )

            latency = int((time.time() - start) * 1000)
            body["latency_ms"] = latency
            body["sse_log"] = session.sse_log + body.get("sse_log", [])
            update_session(thread_id, status=status, sse_log=body["sse_log"])
            return body, 202 if status == "hitl" else 200

    async def _poll_until_done(
        self,
        client: BandClient,
        room_id: str,
        thread_id: str,
        sse_log: list,
        start: float,
        *,
        prior_ids: set[str] | None = None,
        context_min_index: int = 0,
        self_name: str | None = None,
    ) -> tuple[str, dict]:
        """Poll room context every 2s until a terminal message arrives or timeout."""
        seen: set[str] = set(prior_ids) if prior_ids else set()
        deadline = start + _handoff_timeout()

        while time.time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            result = await self._poll_context(
                client, room_id, seen, sse_log, thread_id,
                context_min_index=context_min_index,
                self_name=self_name,
            )
            if result:
                return result

        raise TimeoutError(
            f"band handoff timed out after {_handoff_timeout()}s — are all 3 agents running?"
        )

    async def _poll_context(
        self,
        client: BandClient,
        room_id: str,
        seen: set[str],
        sse_log: list,
        thread_id: str,
        *,
        context_min_index: int = 0,
        self_name: str | None = None,
    ) -> tuple[str, dict] | None:
        try:
            ctx = await client.get_context(room_id)
            for i, msg in enumerate(ctx.data):
                if i < context_min_index:
                    continue
                key = _msg_key(msg)
                if key in seen:
                    continue
                seen.add(key)
                content = msg.content or ""
                sender = msg.sender_name
                # Never react to our own outgoing messages — the gateway relays the
                # carried case payload to the intake agent, and that payload can contain
                # keys (follow_up_questions, doctor_brief) that would otherwise be
                # misclassified as an agent's terminal reply.
                if self_name and sender and sender.strip().lower() == self_name.strip().lower():
                    continue
                data = extract_json_payload(content) or {}
                if is_hitl_message(content, sender):
                    # Save full intake state so next /respond round can pass Q&A context back
                    update_session(thread_id, last_intake_payload=data)
                    return ("hitl", build_hitl_response(data, thread_id, sse_log))
                if is_complete_message(content, sender):
                    return ("complete", build_result_from_payload(data, thread_id, 0))
        except Exception as exc:
            logger.debug("context poll failed: %s", exc)
        return None

    async def check_gateway(self) -> dict:
        gateway = load_agent_credentials("gateway")
        if not gateway:
            return {"configured": False, "connected": False}
        try:
            async with BandClient(gateway) as client:
                me = await client.me()
                name = getattr(me, "name", None)
                if not name and hasattr(me, "data"):
                    name = getattr(me.data, "name", None)
                return {"configured": True, "connected": True, "agent_name": name, "mode": "band"}
        except Exception as exc:
            return {"configured": True, "connected": False, "error": str(exc), "mode": "band"}
