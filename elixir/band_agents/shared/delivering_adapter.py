"""Direct-routing Band adapter — calls Elixire tools without an LLM routing loop."""
from __future__ import annotations

import json
import logging

from band.adapters import LangGraphAdapter
from band.core.protocols import AgentToolsProtocol

logger = logging.getLogger("elixire.band.delivering_adapter")

# Module-level: persists across reconnects within the same process.
# Prevents a message from being reprocessed when Band re-delivers it after
# session.already_connected evictions.
_PROCESSED_ONCE: set[str] = set()

HANDLES = {
    "receptionist": "nothariharan/elixir-intake",    # Band handle for the receptionist agent
    "intake":       "nothariharan/elixir-clinical",  # Band handle for the intake agent
    "brief":        "nothariharan/elixir-action",    # Band handle for the brief agent
    "gateway":      "nothariharan/elixir-gateway",
}


def _extract_json_from_message(content: str) -> dict | None:
    """Find the first complete JSON object in a Band message (handles ```json blocks)."""
    # Prefer content inside ```json blocks
    block_start = content.find("```json")
    search_from = 0
    if block_start >= 0:
        j = content.find("{", block_start)
        if j >= 0:
            search_from = j

    i = search_from
    while i < len(content):
        idx = content.find("{", i)
        if idx < 0:
            break
        try:
            obj, _ = json.JSONDecoder().raw_decode(content[idx:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        i = idx + 1

    return None


class DeliveringLangGraphAdapter(LangGraphAdapter):
    """Elixire Band adapter — routes messages directly to node tools, no LLM loop."""

    def __init__(self, *args, role: str = "intake", **kwargs):
        super().__init__(*args, **kwargs)
        self.role = role
        # kept for API compatibility (callers may inspect these)
        self._used_send_message = False
        self._last_ai_text: str | None = None
        self._send_attempted = False

    async def on_message(
        self,
        msg,
        tools: AgentToolsProtocol,
        history,
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        msg_id = getattr(msg, "id", None)
        if msg_id:
            if msg_id in _PROCESSED_ONCE:
                logger.info("room %s [%s]: skipping already-processed message %s", room_id[:8], self.role, msg_id)
                return
            _PROCESSED_ONCE.add(msg_id)

        content = getattr(msg, "content", "") or ""
        case_data = _extract_json_from_message(content)

        if case_data is None:
            logger.warning("room %s [%s]: no JSON payload in message — ignoring", room_id[:8], self.role)
            return

        try:
            if self.role == "receptionist":
                await self._direct_receptionist(case_data, tools, room_id)
            elif self.role == "intake":
                await self._direct_intake(case_data, tools, room_id)
            elif self.role == "brief":
                await self._direct_brief(case_data, tools, room_id)
            else:
                logger.warning("room %s: unknown role %r — dropping message", room_id[:8], self.role)
        except Exception as exc:
            logger.error("room %s [%s]: handler error: %s", room_id[:8], self.role, exc, exc_info=True)
            await tools.send_message(
                json.dumps({"elixire_status": "error", "error": str(exc)}),
                mentions=[HANDLES["gateway"]],
            )

    # ── per-role handlers ──────────────────────────────────────────────────────

    async def _direct_receptionist(
        self, case_data: dict, tools: AgentToolsProtocol, room_id: str
    ) -> None:
        from band_agents.shared.tools import run_receptionist

        logger.info("room %s: receptionist validating session", room_id[:8])
        try:
            result_str = run_receptionist.invoke({"case_json": json.dumps(case_data)})
            result = json.loads(result_str)
        except Exception as exc:
            logger.error("room %s: run_receptionist raised: %s", room_id[:8], exc)
            await tools.send_message(
                json.dumps({"elixire_status": "error", "error": str(exc)}),
                mentions=[HANDLES["gateway"]],
            )
            return

        if not result.get("is_valid", False):
            err = result.get("error") or "Please clarify why you are visiting."
            logger.info("room %s: session invalid — %s", room_id[:8], err)
            await tools.send_message(
                json.dumps({**result, "elixire_status": "invalid"}, ensure_ascii=False),
                mentions=[HANDLES["gateway"]],
            )
        else:
            handoff = {**case_data, **result}
            logger.info("room %s: session valid — routing to intake", room_id[:8])
            await tools.send_message(
                json.dumps(handoff, ensure_ascii=False),
                mentions=[HANDLES["intake"]],
            )

    async def _direct_intake(
        self, case_data: dict, tools: AgentToolsProtocol, room_id: str
    ) -> None:
        from band_agents.shared.tools import run_document_processor, run_intake_conversation

        logger.info("room %s: intake conversation starting", room_id[:8])
        try:
            result_str = run_intake_conversation.invoke({"case_json": json.dumps(case_data)})
            result = json.loads(result_str)
        except Exception as exc:
            logger.error("room %s: run_intake_conversation raised: %s", room_id[:8], exc)
            await tools.send_message(
                json.dumps({"elixire_status": "error", "error": str(exc)}),
                mentions=[HANDLES["gateway"]],
            )
            return

        status = result.get("elixire_status", "unknown")
        handoff = {**case_data, **result}

        if status in ("hitl", "emergency"):
            logger.info("room %s: intake → %s — routing to gateway", room_id[:8], status)
            await tools.send_message(
                json.dumps(handoff, ensure_ascii=False),
                mentions=[HANDLES["gateway"]],
            )
            return

        # ready_for_documents — optionally process uploaded files
        if case_data.get("uploaded_documents"):
            logger.info("room %s: processing uploaded documents", room_id[:8])
            try:
                doc_str = run_document_processor.invoke({"case_json": json.dumps(handoff)})
                doc_result = json.loads(doc_str)
                handoff.update(doc_result)
            except Exception as exc:
                logger.warning("room %s: document processing failed (continuing): %s", room_id[:8], exc)

        logger.info("room %s: intake complete — routing to brief", room_id[:8])
        await tools.send_message(
            json.dumps(handoff, ensure_ascii=False),
            mentions=[HANDLES["brief"]],
        )

    async def _direct_brief(
        self, case_data: dict, tools: AgentToolsProtocol, room_id: str
    ) -> None:
        from band_agents.shared.tools import (
            run_brief_generator,
            run_prescription_generator,
            run_prescription_verifier,
        )

        status = case_data.get("elixire_status", "")

        if status == "consultation":
            # Post-consultation: verify then generate prescription
            logger.info("room %s: brief generating prescription", room_id[:8])
            try:
                ver_str = run_prescription_verifier.invoke({"case_json": json.dumps(case_data)})
                ver = json.loads(ver_str)
                merged = {**case_data, **ver}

                gen_str = run_prescription_generator.invoke({"case_json": json.dumps(merged)})
                gen = json.loads(gen_str)
                handoff = {**merged, **gen}
            except Exception as exc:
                logger.error("room %s: prescription pipeline raised: %s", room_id[:8], exc)
                await tools.send_message(
                    json.dumps({"elixire_status": "error", "error": str(exc)}),
                    mentions=[HANDLES["gateway"]],
                )
                return

            await tools.send_message(
                json.dumps(handoff, ensure_ascii=False),
                mentions=[HANDLES["gateway"]],
            )
        else:
            # Pre-consultation: generate doctor brief
            logger.info("room %s: brief generating doctor brief", room_id[:8])
            try:
                result_str = run_brief_generator.invoke({"case_json": json.dumps(case_data)})
                result = json.loads(result_str)
            except Exception as exc:
                logger.error("room %s: run_brief_generator raised: %s", room_id[:8], exc)
                await tools.send_message(
                    json.dumps({"elixire_status": "error", "error": str(exc)}),
                    mentions=[HANDLES["gateway"]],
                )
                return

            handoff = {**case_data, **result}
            await tools.send_message(
                json.dumps(handoff, ensure_ascii=False),
                mentions=[HANDLES["gateway"]],
            )


# kept for any external callers that import these helpers
def _message_content(output) -> str | None:
    if output is None:
        return None
    content = getattr(output, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(output, dict):
        raw = output.get("content")
        if isinstance(raw, str) and raw.strip():
            return raw
    return None


def _mention_target(text: str, role: str) -> str:
    lower = text.lower()
    if role == "receptionist":
        return HANDLES["intake"]
    if role == "intake":
        if "elixire_status" in lower and "hitl" in lower:
            return HANDLES["gateway"]
        if "follow_up_questions" in lower or "follow-up" in lower or "follow up" in lower:
            return HANDLES["gateway"]
        return HANDLES["brief"]
    if role == "brief":
        return HANDLES["gateway"]
    return HANDLES["gateway"]
