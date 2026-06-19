"""thread_id ↔ band room session state."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BandSession:
    thread_id: str
    room_id: str
    raw_input: str
    mode: str
    locale: str
    patient_history: list
    follow_up_count: int = 0
    patient_responses: list = field(default_factory=list)
    sse_log: list = field(default_factory=list)
    model_provider_log: list = field(default_factory=list)
    status: str = "running"
    # Carry full intake state across HITL rounds so questions pair with answers
    last_intake_payload: dict = field(default_factory=dict)


_sessions: dict[str, BandSession] = {}


def create_session(**kwargs) -> BandSession:
    session = BandSession(**kwargs)
    _sessions[session.thread_id] = session
    return session


def get_session(thread_id: str) -> BandSession | None:
    return _sessions.get(thread_id)


def update_session(thread_id: str, **kwargs) -> BandSession | None:
    session = _sessions.get(thread_id)
    if not session:
        return None
    for k, v in kwargs.items():
        setattr(session, k, v)
    return session
