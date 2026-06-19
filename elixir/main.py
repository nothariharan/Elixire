import asyncio
import hashlib, json, time, uuid, base64, os, sys, subprocess, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from contextlib import asynccontextmanager
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from langgraph.checkpoint.memory import MemorySaver
from graph import build_graph
from pdf_ingest import parse_patient_pdf
from nodes.emergency import emergency_node, EMERGENCY_MESSAGE, CRISIS_RESOURCES
from nodes.guard import guard_node
from clinic_protocol.schema import ClinicProtocol
from clinic_protocol.loader import save_protocol, load_protocol, load_default
from gateway.config import all_agents_configured, load_agent_credentials
from gateway.orchestrator import BandOrchestrator
from llm_client import LLM_PROVIDER, aws_configured, ORCHESTRATOR_MODEL_ID
import database as db

logger = logging.getLogger("elixire.main")

env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

USE_LEGACY = os.getenv("ELIXIR_LEGACY_GRAPH", "0") == "1"
USE_BAND = not USE_LEGACY and all_agents_configured()

# ── band agent supervisor ─────────────────────────────────────────────────────

_BAND_AGENTS_DIR = Path(__file__).parent / "band_agents"
_AGENT_LOGS_DIR = _BAND_AGENTS_DIR / "logs"
_AGENT_ROLES = ["receptionist", "intake", "brief"]
_agent_procs: dict[str, subprocess.Popen] = {}
_agent_log_fds: dict[str, object] = {}
_watchdog_task: asyncio.Task | None = None
_agents_started_at: float = 0.0
_agents_starting: bool = False
_AGENT_STAGGER_SECS = 12  # seconds between each agent start


def _start_agent(role: str) -> subprocess.Popen:
    script = _BAND_AGENTS_DIR / role / "agent.py"
    _AGENT_LOGS_DIR.mkdir(exist_ok=True)
    log_path = _AGENT_LOGS_DIR / f"{role}.log"
    # Close previous log fd if open
    old_fd = _agent_log_fds.get(role)
    if old_fd:
        try:
            old_fd.close()
        except Exception:
            pass
    log_fd = open(log_path, "a", buffering=1)
    _agent_log_fds[role] = log_fd
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(_BAND_AGENTS_DIR / role),
        stdout=log_fd,
        stderr=log_fd,
    )
    logger.info("band agent started: %s (pid %d) log=%s", role, proc.pid, log_path)
    return proc


_agent_crash_count: dict[str, int] = {}
_agent_next_retry: dict[str, float] = {}


async def _watchdog() -> None:
    """Restart crashed band agents with non-blocking exponential backoff.

    Each agent's backoff is tracked independently — no sleep inside the loop,
    so a long backoff for one agent never delays recovery of another.
    State machine per agent:
      running  → proc.poll() is None
      crashed  → poll() is not None, next_retry == 0  → schedule backoff
      waiting  → poll() is not None, now < next_retry → skip
      restart  → poll() is not None, now >= next_retry > 0 → start agent
    """
    while True:
        await asyncio.sleep(15)
        # Don't interfere while staggered start is in progress or before any start
        if _agents_starting or _agents_started_at == 0:
            continue
        now = time.time()
        for role in _AGENT_ROLES:
            proc = _agent_procs.get(role)
            if proc and proc.poll() is None:
                continue  # healthy

            next_retry = _agent_next_retry.get(role, 0)

            if next_retry and now < next_retry:
                continue  # still in backoff window

            if not next_retry:
                # First detection of this crash — schedule backoff, don't restart yet
                crashes = _agent_crash_count.get(role, 0) + 1
                _agent_crash_count[role] = crashes
                backoff = min(30 * (2 ** (crashes - 1)), 120)
                _agent_next_retry[role] = now + backoff
                code = proc.returncode if proc else "?"
                logger.warning(
                    "band %s crashed (code=%s, #%d) — retry in %ds",
                    role, code, crashes, backoff,
                )
                # Hint: tail the log
                logger.info("  tail %s", _AGENT_LOGS_DIR / f"{role}.log")
                continue

            # Backoff expired — restart now (run Popen in a thread so we don't block the loop)
            crashes = _agent_crash_count.get(role, 0)
            logger.info("band %s restarting after backoff (crash #%d)", role, crashes)
            _agent_procs[role] = await asyncio.to_thread(_start_agent, role)
            _agent_next_retry[role] = 0  # arm for next crash detection
            _agent_crash_count[role] = max(0, crashes - 1)


def _seed_default_clinic() -> None:
    """Auto-configure a general practice clinic on first startup."""
    existing = db.get_clinic("general_practice")
    if existing:
        return
    protocol = load_default("general_practice")
    db.upsert_clinic(
        clinic_id="general_practice",
        clinic_name="Elixire General Clinic",
        specialty=protocol.specialty if protocol else "general_practice",
        doctor_name="Dr. Priya Sharma",
        doctor_qualifications="MBBS, MD (General Practice)",
        clinic_address="123 Health Street, Wellness City",
        clinic_phone="+1-555-0100",
    )
    logger.info("seeded default general_practice clinic")


async def _staggered_start_agents() -> None:
    """Kill any running agents, clear logs, start fresh with 12s stagger (background task)."""
    global _agents_started_at, _agents_starting
    _agents_starting = True
    try:
        # Terminate any currently running agent processes
        for role, proc in list(_agent_procs.items()):
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass
        await asyncio.sleep(2)
        _agent_procs.clear()

        # Wipe old logs so _agent_log_ready() detects only the fresh run
        _AGENT_LOGS_DIR.mkdir(exist_ok=True)
        for role in _AGENT_ROLES:
            log_path = _AGENT_LOGS_DIR / f"{role}.log"
            try:
                log_path.write_text("")
            except Exception:
                pass

        # Staggered start — 12s between each agent to avoid thundering-herd 429s
        for i, role in enumerate(_AGENT_ROLES):
            if i > 0:
                await asyncio.sleep(_AGENT_STAGGER_SECS)
            _agent_procs[role] = await asyncio.to_thread(_start_agent, role)
            logger.info("staggered start: %s (pid %d)", role, _agent_procs[role].pid)

        _agents_started_at = time.time()
        logger.info("all 3 agents launched; waiting for Band connections…")
    finally:
        _agents_starting = False


def _agent_log_ready(role: str) -> bool:
    """Return True when the process is alive AND log contains 'Agent started:'."""
    proc = _agent_procs.get(role)
    if not proc or proc.poll() is not None:
        return False
    log_path = _AGENT_LOGS_DIR / f"{role}.log"
    try:
        return "Agent started:" in log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _watchdog_task
    db.init_db()
    _seed_default_clinic()

    if USE_BAND:
        _watchdog_task = asyncio.create_task(_watchdog())
        logger.info("band mode ready — agents start on first patient tap")

    yield  # server runs here

    # shutdown: stop watchdog then terminate agents
    if _watchdog_task:
        _watchdog_task.cancel()
    for role, proc in _agent_procs.items():
        proc.terminate()
        logger.info("band agent stopped: %s", role)


app = FastAPI(
    title="Elixire",
    description="AI Workforce for Solo Clinics — Band Multi-Agent System",
    lifespan=lifespan,
)

# cors — allow browser ui to call api
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_cache: dict[str, dict] = {}
checkpointer = MemorySaver()
_orchestrator = BandOrchestrator() if USE_BAND else None

# mount static files for the ui
STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    # Simple "E" SVG favicon — no file needed
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="8" fill="#6c63ff"/>'
        '<text x="16" y="23" font-family="sans-serif" font-size="20" font-weight="bold" '
        'fill="white" text-anchor="middle">E</text>'
        '</svg>'
    )
    return Response(content=svg, media_type="image/svg+xml")


def _cache_key(symptoms: list[str]) -> str:
    normalized = sorted([s.lower().strip() for s in symptoms])
    return hashlib.sha256(json.dumps(normalized).encode()).hexdigest()


def _ingest_pdf(b64_pdf: str | None) -> list[dict]:
    if not b64_pdf:
        return []
    try:
        raw_bytes = base64.b64decode(b64_pdf)
        return parse_patient_pdf(raw_bytes)
    except Exception:
        return []


def _sanitize_result(result: dict) -> dict:
    """make result json-serializable by converting sets to lists."""
    sanitized = {}
    for k, v in result.items():
        if isinstance(v, set):
            sanitized[k] = list(v)
        elif isinstance(v, list):
            sanitized[k] = [
                {kk: list(vv) if isinstance(vv, set) else vv for kk, vv in item.items()}
                if isinstance(item, dict) else item
                for item in v
            ]
        else:
            sanitized[k] = v
    return sanitized


def _build_audit_trail(result: dict) -> dict:
    """build the provenance/audit trail from the pipeline result."""
    snippets = result.get("clustered_snippets", [])
    tier_breakdown = {"canonical": 0, "literature": 0, "contextual": 0}
    for s in snippets:
        if isinstance(s, dict):
            tier = s.get("source_tier", "contextual")
            tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1

    return {
        "model_provider_log": result.get("model_provider_log", []),
        "provenance": result.get("provenance", []),
        "canonical_terms": result.get("canonical_terms", {}),
        "source_tier_breakdown": tier_breakdown,
    }


def _local_guard_emergency(raw_input: str, thread_id: str) -> dict | None:
    """run guard + emergency locally before any band call."""
    state = {
        "raw_input": raw_input,
        "sse_log": [],
        "emergency_flag": False,
        "emergency_reason": None,
    }
    state = guard_node(state)
    if not state.get("is_valid"):
        return {"error": state.get("error"), "invalid": True, "sse_log": state.get("sse_log", [])}

    state = emergency_node(state)
    if state.get("emergency_flag"):
        reason = state.get("emergency_reason", "")
        is_crisis = "crisis" in reason.lower() if reason else False
        resp = {
            "status": "emergency",
            "thread_id": thread_id,
            "message": CRISIS_RESOURCES["message"] if is_crisis else EMERGENCY_MESSAGE,
            "reason": reason,
            "action": "crisis_line" if is_crisis else "seek_immediate_care",
            "sse_log": state.get("sse_log", []),
            "model_provider_log": [],
        }
        if is_crisis:
            resp["helplines"] = CRISIS_RESOURCES["helplines"]
        return resp
    return None


# ── root — serve patient portal ───────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def root():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Elixire</h1><p>Patient portal not found.</p>")


# ── doctor dashboard ──────────────────────────────────────────────────────────
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    dash_path = STATIC_DIR / "dashboard.html"
    if dash_path.exists():
        return HTMLResponse(content=dash_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Elixire Dashboard</h1><p>Dashboard not found.</p>")


# ── /tts — text-to-speech via edge-tts (Microsoft neural voices, free) ────────
@app.get("/tts")
async def text_to_speech(
    text: str = Query(..., max_length=1000),
    voice: str = Query(default="en-IN-NeerjaNeural"),
):
    """Stream MP3 audio for the given text using Microsoft neural TTS (no API key)."""
    import re
    import edge_tts

    # Strip markdown/HTML tags so the voice doesn't read them aloud
    clean = re.sub(r"[*_`#\[\]<>]", "", text).strip()
    clean = re.sub(r"\s+", " ", clean)
    if not clean:
        return JSONResponse(status_code=400, content={"error": "empty text"})

    async def audio_generator():
        communicate = edge_tts.Communicate(clean, voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]

    return StreamingResponse(
        audio_generator(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "no-cache", "X-Voice": voice},
    )


# ── sessions — list / get ─────────────────────────────────────────────────────
@app.get("/sessions")
async def list_sessions(clinic_id: str = Query(default=None)):
    sessions = db.list_sessions(clinic_id=clinic_id)
    # strip chat_log from list view for performance
    for s in sessions:
        s.pop("chat_log", None)
    return {"sessions": sessions}


@app.get("/sessions/{thread_id}")
async def get_session(thread_id: str):
    session = db.get_session(thread_id)
    if not session:
        return JSONResponse(status_code=404, content={"error": "session not found"})
    return session


# ── patients ─────────────────────────────────────────────────────────────────
@app.get("/patients")
async def list_patients(clinic_id: str = Query(default=None)):
    return {"patients": db.list_patients(clinic_id=clinic_id)}


@app.get("/patients/{patient_id}")
async def get_patient(patient_id: str):
    patient = db.get_patient(patient_id)
    if not patient:
        return JSONResponse(status_code=404, content={"error": "patient not found"})
    sessions = db.list_sessions_by_patient(patient_id)
    for s in sessions:
        s.pop("chat_log", None)
    patient["sessions"] = sessions
    patient["prescriptions"] = db.list_prescriptions(patient_id=patient_id)
    patient["receipts"] = db.list_receipts(patient_id=patient_id)
    return patient


# ── prescriptions ─────────────────────────────────────────────────────────────
@app.get("/prescriptions")
async def list_prescriptions(clinic_id: str = Query(default=None), patient_id: str = Query(default=None)):
    return {"prescriptions": db.list_prescriptions(clinic_id=clinic_id, patient_id=patient_id)}


@app.get("/prescriptions/{thread_id}")
async def get_prescription_for_session(thread_id: str):
    rx = db.get_prescription_by_session(thread_id)
    if not rx:
        return JSONResponse(status_code=404, content={"error": "no prescription for this session"})
    return rx


# ── receipts ─────────────────────────────────────────────────────────────────
@app.get("/receipts")
async def list_receipts(clinic_id: str = Query(default=None), patient_id: str = Query(default=None)):
    return {"receipts": db.list_receipts(clinic_id=clinic_id, patient_id=patient_id)}


@app.get("/receipts/{thread_id}")
async def get_receipt_for_session(thread_id: str):
    receipt = db.get_receipt_by_session(thread_id)
    if not receipt:
        return JSONResponse(status_code=404, content={"error": "no receipt for this session"})
    return receipt


# ── agent readiness ──────────────────────────────────────────────────────────
@app.get("/agents/status")
async def agents_status():
    if not USE_BAND:
        return {"ready": True, "mode": "legacy"}
    alive = {role: (_agent_procs.get(role) is not None and _agent_procs[role].poll() is None)
             for role in _AGENT_ROLES}
    elapsed = time.time() - _agents_started_at if _agents_started_at else 0
    log_ready = {role: _agent_log_ready(role) for role in _AGENT_ROLES}
    # Intake needs receptionist + intake connected; brief joins later
    intake_ready = log_ready.get("receptionist", False) and log_ready.get("intake", False)
    return {
        "ready": intake_ready,
        "all_ready": all(log_ready.values()),
        "starting": _agents_starting,
        "mode": "band",
        "processes": alive,
        "log_ready": log_ready,
        "seconds_since_start": round(elapsed, 1),
    }


@app.post("/agents/start")
async def start_agents():
    """Kick off staggered Band agent startup (called when patient taps the mic)."""
    if not USE_BAND:
        return {"status": "legacy", "message": "Band mode not configured"}
    if _agents_starting:
        return {"status": "already_starting", "message": "Agents are already starting up"}
    asyncio.create_task(_staggered_start_agents())
    return {"status": "starting", "message": "Agents starting — poll /agents/status for readiness"}


@app.post("/agents/stop")
async def stop_agents():
    """Terminate all Band agent processes (called after session ends or on demand)."""
    global _agents_started_at
    for role, proc in list(_agent_procs.items()):
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
    _agent_procs.clear()
    _agents_started_at = 0.0
    return {"status": "stopped"}


# ── health check ─────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    band_status = {"mode": "legacy" if USE_LEGACY else ("band" if USE_BAND else "band_unconfigured")}
    if USE_BAND and _orchestrator:
        band_status.update(await _orchestrator.check_gateway())

    agents = {
        role: ("configured" if load_agent_credentials(role) else "missing")
        for role in ("receptionist", "intake", "brief", "gateway")
    }

    from clinic_protocol.loader import list_protocols, _DEFAULTS_DIR
    specialties = sorted(p.stem for p in _DEFAULTS_DIR.glob("*.json"))

    return {
        "status": "ok",
        "version": "elixire-1.0",
        "band": band_status,
        "agents": agents,
        "llm": {
            "provider": LLM_PROVIDER,
            "featherless": "configured" if os.getenv("FEATHERLESS_API_KEY", "").strip() else "missing",
        },
        "aml": "configured" if os.getenv("AML_API_KEY", "").strip() else "missing",
        "emergency_gate": "active",
        "specialties": specialties,
    }


@app.get("/debug/env")
async def debug_env():
    """Full environment key audit — for debugging only."""
    def _check(key: str) -> str:
        val = os.getenv(key, "")
        if not val:
            return "missing"
        if val.startswith("YOUR_") or val.startswith("tvly-YOUR"):
            return "placeholder"
        return "configured"

    return {
        "FEATHERLESS_API_KEY": _check("FEATHERLESS_API_KEY"),
        "FEATHERLESS_MODEL_TRIAGE": _check("FEATHERLESS_MODEL_TRIAGE"),
        "FEATHERLESS_MODEL_ACTION": _check("FEATHERLESS_MODEL_ACTION"),
        "AML_API_KEY": _check("AML_API_KEY"),
        "AML_BASE_URL": _check("AML_BASE_URL"),
        "AML_MODEL_VERIFICATION": _check("AML_MODEL_VERIFICATION"),
        "UMLS_API_KEY": _check("UMLS_API_KEY"),
        "TAVILY_API_KEY": _check("TAVILY_API_KEY"),
        "BRAVE_API_KEY": _check("BRAVE_API_KEY"),
        "SEMANTIC_SCHOLAR_API_KEY": _check("SEMANTIC_SCHOLAR_API_KEY"),
        "RESEARCH_EMAIL": _check("RESEARCH_EMAIL"),
        "OPENALEX_EMAIL": _check("OPENALEX_EMAIL"),
        "REDDIT_CLIENT_ID": _check("REDDIT_CLIENT_ID"),
        "REDDIT_CLIENT_SECRET": _check("REDDIT_CLIENT_SECRET"),
        "LLM_PROVIDER": os.getenv("LLM_PROVIDER", "featherless"),
        "ELIXIR_LEGACY_GRAPH": os.getenv("ELIXIR_LEGACY_GRAPH", "0"),
        "AWS_ACCESS_KEY_ID": _check("AWS_ACCESS_KEY_ID"),
        "ORCHESTRATOR_MODEL_ID": _check("ORCHESTRATOR_MODEL_ID"),
    }


# ── /diagnose ────────────────────────────────────────────────────────────────
@app.post("/diagnose")
async def diagnose(request: Request):
    body = await request.json()
    raw_input: str = body.get("symptoms", "")
    mode: str = body.get("mode", "fast")
    locale: str = body.get("locale", "en")
    patient_pdf: str | None = body.get("patient_pdf")

    key = _cache_key(raw_input.split(","))
    if key in _cache:
        return _cache[key]

    thread_id = str(uuid.uuid4())
    patient_history = _ingest_pdf(patient_pdf)

    pre = _local_guard_emergency(raw_input, thread_id)
    if pre:
        if pre.get("invalid"):
            return JSONResponse(status_code=400, content={"error": pre["error"], "sse_log": pre["sse_log"]})
        return JSONResponse(status_code=200, content=pre)

    if USE_BAND and _orchestrator:
        try:
            result, code = await _orchestrator.run_case(
                thread_id, raw_input, mode, locale, patient_history
            )
            if code == 202:
                return JSONResponse(status_code=202, content=result)
            result["audit_trail"] = result.get("audit_trail") or _build_audit_trail(result)
            sanitized = _sanitize_result(result)
            _cache[key] = sanitized
            return sanitized
        except TimeoutError as e:
            return JSONResponse(status_code=504, content={
                "status": "error",
                "error": str(e),
                "hint": "run: python band_agents/run_all.py",
            })
        except Exception as e:
            return JSONResponse(status_code=500, content={"status": "error", "error": str(e)})

    # legacy in-process langgraph fallback
    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    start = time.time()
    result = await graph.ainvoke(
        {
            "raw_input": raw_input,
            "mode": mode,
            "locale": locale,
            "thread_id": thread_id,
            "patient_history_timeline": patient_history,
            "patient_responses": [],
            "follow_up_count": 0,
            "follow_up_questions": [],
            "sse_log": [],
            "model_provider_log": [],
            "emergency_flag": False,
            "emergency_reason": None,
            "provenance": [],
            "canonical_terms": {},
        },
        config=config,
    )

    if result.get("follow_up_questions"):
        return JSONResponse(status_code=202, content={
            "status": "requires_action",
            "thread_id": thread_id,
            "questions": result["follow_up_questions"],
            "sse_log": result.get("sse_log", []),
            "model_provider_log": result.get("model_provider_log", []),
        })

    result["latency_ms"] = int((time.time() - start) * 1000)
    result["thread_id"] = thread_id
    result["audit_trail"] = _build_audit_trail(result)
    sanitized = _sanitize_result(result)
    _cache[key] = sanitized
    return sanitized


# ── /respond ─────────────────────────────────────────────────────────────────
@app.post("/respond")
async def respond(request: Request):
    body = await request.json()
    thread_id: str = body["thread_id"]
    answer: str = body["answer"]

    db.append_message(thread_id, "patient", answer)

    if USE_BAND and _orchestrator:
        try:
            result, code = await _orchestrator.resume_case(thread_id, answer)
            if code == 202:
                _persist_respond_result(thread_id, result)
                return JSONResponse(status_code=202, content=result)
            result["audit_trail"] = result.get("audit_trail") or _build_audit_trail(result)
            _persist_respond_result(thread_id, result)
            return _sanitize_result(result)
        except KeyError:
            return JSONResponse(status_code=404, content={"error": "unknown thread_id"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"status": "error", "error": str(e)})

    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    current_responses = snapshot.values.get("patient_responses", [])
    current_count = snapshot.values.get("follow_up_count", 0)

    result = await graph.ainvoke(
        {
            "patient_responses": current_responses + [answer],
            "follow_up_count": current_count + 1,
            "follow_up_questions": [],
        },
        config=config,
    )

    if result.get("follow_up_questions"):
        questions = result["follow_up_questions"]
        for q in questions:
            db.append_message(thread_id, "ai", q, agent="Elixire-Intake")
        return JSONResponse(status_code=202, content={
            "status": "requires_action",
            "thread_id": thread_id,
            "questions": questions,
            "sse_log": result.get("sse_log", []),
            "model_provider_log": result.get("model_provider_log", []),
        })

    _persist_respond_result(thread_id, result)
    result["thread_id"] = thread_id
    result["audit_trail"] = _build_audit_trail(result)
    return _sanitize_result(result)


def _persist_respond_result(thread_id: str, result: dict) -> None:
    status = result.get("status", "")
    brief = result.get("doctor_brief")
    questions = result.get("questions", [])
    for q in questions:
        db.append_message(thread_id, "ai", q, agent="Elixire-Intake")
    if brief:
        db.update_session(thread_id, status="brief_ready", doctor_brief=brief)
    elif status in ("complete",):
        db.update_session(thread_id, status="consulted")


# ── /stream — legacy sse only ────────────────────────────────────────────────
@app.get("/stream")
async def stream(request: Request, symptoms: str, mode: str = "fast",
                 locale: str = "en", thread_id: str = None):
    tid = thread_id or str(uuid.uuid4())
    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": tid}}

    async def event_generator():
        async for event in graph.astream(
            {"raw_input": symptoms, "mode": mode, "locale": locale,
             "thread_id": tid, "patient_responses": [], "follow_up_count": 0,
             "follow_up_questions": [], "patient_history_timeline": [], "sse_log": [],
             "model_provider_log": [], "emergency_flag": False, "emergency_reason": None,
             "provenance": [], "canonical_terms": {}},
            config=config,
        ):
            for node_name, state in event.items():
                for msg in state.get("sse_log", []):
                    yield {"data": json.dumps({"node": node_name, "msg": msg, "via_band": USE_BAND})}

                if state.get("emergency_flag"):
                    yield {"data": json.dumps({
                        "type": "emergency",
                        "message": EMERGENCY_MESSAGE,
                        "reason": state.get("emergency_reason", ""),
                    })}

                if state.get("follow_up_questions"):
                    yield {"data": json.dumps({
                        "type": "requires_action",
                        "thread_id": tid,
                        "questions": state["follow_up_questions"],
                    })}

    return EventSourceResponse(event_generator())


# ── /intake — Elixire patient session start (alias + extension of /diagnose) ───
@app.post("/intake")
async def intake(request: Request):
    """Start a patient intake session. Triggers Receptionist → Intake → Brief agent chain."""
    body = await request.json()
    # Map Elixire request shape to existing orchestrator shape
    raw_input: str = body.get("message", body.get("symptoms", ""))
    locale: str = body.get("locale", "en")
    mode: str = body.get("mode", "fast")
    patient_name: str = body.get("patient_name", "")
    patient_dob: str = body.get("patient_dob", "")
    patient_contact: str = body.get("patient_contact", "")
    appointment_type: str = body.get("appointment_type", "general_consultation")
    clinic_id: str = body.get("clinic_id", "general_practice")
    uploaded_documents: list = body.get("uploaded_documents", [])
    patient_pdf: str | None = body.get("patient_pdf")

    thread_id = str(uuid.uuid4())
    patient_history = _ingest_pdf(patient_pdf)

    # Emergency check before any agent
    pre = _local_guard_emergency(raw_input or patient_name or "checkup", thread_id)
    if pre and pre.get("status") == "emergency":
        return JSONResponse(status_code=200, content=pre)

    # Persist patient record (deduped by clinic + name + dob)
    patient_id = ""
    if patient_name:
        patient_id = db.upsert_patient(clinic_id, patient_name, patient_dob, patient_contact)

    # Persist session to DB
    db.create_session(thread_id, clinic_id, patient_name, patient_dob, patient_contact, appointment_type, patient_id=patient_id)
    if raw_input:
        db.append_message(thread_id, "patient", raw_input)

    # Load clinic protocol
    protocol = load_protocol(clinic_id)
    clinic_protocol_dict = protocol.model_dump() if protocol else {}

    # Build enriched payload for Band orchestrator
    enriched_input = json.dumps({
        "patient_name": patient_name,
        "patient_dob": patient_dob,
        "patient_contact": patient_contact,
        "appointment_type": appointment_type,
        "clinic_id": clinic_id,
        "clinic_protocol": clinic_protocol_dict,
        "uploaded_documents": uploaded_documents,
        "message": raw_input,
        "locale": locale,
    })

    if USE_BAND and _orchestrator:
        try:
            result, code = await _orchestrator.run_case(
                thread_id, enriched_input, mode, locale, patient_history
            )
            result["thread_id"] = thread_id
            # Persist any questions or brief that came back immediately (no HITL round-trip)
            _persist_respond_result(thread_id, result)
            return JSONResponse(status_code=code, content=_sanitize_result(result))
        except TimeoutError as e:
            return JSONResponse(status_code=504, content={
                "status": "error", "error": str(e),
                "hint": "run: python band_agents/run_all.py",
            })
        except Exception as e:
            return JSONResponse(status_code=500, content={"status": "error", "error": str(e)})

    return JSONResponse(status_code=200, content={
        "thread_id": thread_id,
        "status": "legacy_mode",
        "message": "Band not configured. Use ELIXIR_LEGACY_GRAPH=1 or configure band_agents.",
    })


# ── /setup — doctor creates or updates clinic protocol ─────────────────────────
@app.post("/setup")
async def setup_clinic(request: Request):
    """Doctor creates or updates their clinic protocol configuration."""
    body = await request.json()
    try:
        protocol = ClinicProtocol(**body)
        clinic_id = save_protocol(protocol)
        db.upsert_clinic(
            clinic_id,
            protocol.clinic_name,
            protocol.specialty,
            protocol.doctor_name,
            doctor_qualifications=getattr(protocol, "doctor_qualifications", ""),
            clinic_address=getattr(protocol, "clinic_address", ""),
            clinic_phone=getattr(protocol, "clinic_phone", ""),
        )
        return JSONResponse(status_code=200, content={
            "clinic_id": clinic_id,
            "status": "created" if body.get("clinic_id") in (None, "", "new") else "updated",
            "clinic_name": protocol.clinic_name,
            "specialty": protocol.specialty,
            "appointment_types": [a.type_id for a in protocol.appointment_types],
        })
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@app.get("/setup/{clinic_id}")
async def get_clinic_protocol(clinic_id: str):
    """Retrieve a saved clinic protocol."""
    protocol = load_protocol(clinic_id)
    if not protocol:
        return JSONResponse(status_code=404, content={"error": f"Protocol '{clinic_id}' not found"})
    return JSONResponse(status_code=200, content=protocol.model_dump())


@app.get("/defaults/{specialty}")
async def get_default_protocol(specialty: str):
    """Return the bundled default protocol for a specialty (for wizard pre-fill)."""
    protocol = load_default(specialty)
    if not protocol:
        return JSONResponse(status_code=404, content={"error": f"No default protocol for specialty '{specialty}'"})
    return JSONResponse(status_code=200, content=protocol.model_dump())


# ── /consultation — doctor submits post-consultation notes ─────────────────────
@app.post("/consultation")
async def post_consultation(request: Request):
    """Doctor submits consultation results; triggers prescription generation via Brief agent."""
    body = await request.json()
    thread_id: str | None = body.get("thread_id") or None
    if not thread_id:
        return JSONResponse(status_code=400, content={"error": "thread_id is required — start an intake session first"})
    diagnosis: str = body.get("diagnosis", "")
    prescribed_medications: list = body.get("prescribed_medications", [])
    follow_up_date: str = body.get("follow_up_date", "")
    follow_up_instructions: str = body.get("follow_up_instructions", "")
    doctor_notes: str = body.get("doctor_notes", "")
    clinic_id: str = body.get("clinic_id", "general_practice")
    locale: str = body.get("locale", "en")

    protocol = load_protocol(clinic_id)
    clinic_protocol_dict = protocol.model_dump() if protocol else {}

    # Build post-consultation payload
    consultation_payload = json.dumps({
        "phase": "post_consultation",
        "diagnosis": diagnosis,
        "doctor_notes": doctor_notes,
        "prescribed_medications": prescribed_medications,
        "follow_up_date": follow_up_date,
        "follow_up_instructions": follow_up_instructions,
        "clinic_protocol": clinic_protocol_dict,
        "locale": locale,
    })

    if USE_BAND and _orchestrator:
        try:
            result, code = await _orchestrator.resume_case(thread_id, consultation_payload)
            result["thread_id"] = thread_id
            # Mark consulted and persist prescription only after band succeeds
            db.update_session(thread_id, status="consulted", diagnosis=diagnosis)
            _persist_prescription(thread_id, result)
            return JSONResponse(status_code=code, content=_sanitize_result(result))
        except KeyError:
            return JSONResponse(status_code=404, content={"error": "unknown thread_id — start an intake session first"})
        except Exception as e:
            return JSONResponse(status_code=500, content={"status": "error", "error": str(e)})

    # Legacy mode: run prescription nodes directly
    from nodes.prescription_verifier import prescription_verifier_node
    from nodes.prescription_generator import prescription_generator_node

    state = {
        "prescribed_medications": prescribed_medications,
        "allergies": body.get("allergies", []),
        "diagnosis": diagnosis,
        "doctor_notes": doctor_notes,
        "follow_up_date": follow_up_date,
        "follow_up_instructions": follow_up_instructions,
        "patient_name": body.get("patient_name", "Patient"),
        "patient_dob": body.get("patient_dob", ""),
        "patient_contact": body.get("patient_contact", ""),
        "clinic_protocol": clinic_protocol_dict,
        "locale": locale,
        "doctor_brief": body.get("doctor_brief", ""),
        "sse_log": [],
        "model_provider_log": [],
        "provenance": [],
    }
    state = prescription_verifier_node(state)
    state = prescription_generator_node(state)

    db.update_session(thread_id, status="consulted", diagnosis=diagnosis)
    out = {
        "thread_id": thread_id,
        "status": "prescription_ready",
        "prescription_draft": state.get("prescription_draft", ""),
        "prescription_verified": state.get("prescription_verified", False),
        "sse_log": state.get("sse_log", []),
        "model_provider_log": state.get("model_provider_log", []),
    }
    _persist_prescription(thread_id, out)
    return JSONResponse(status_code=200, content=_sanitize_result(out))


def _persist_prescription(thread_id: str, result: dict) -> None:
    draft = result.get("prescription_draft", "")
    verified = result.get("prescription_verified", False)
    draft_str = draft if isinstance(draft, str) else json.dumps(draft)

    # Generate PDF and capture the path
    pdf_path = ""
    try:
        from integrations.pdf_generator import generate_prescription_pdf
        pdf_path = generate_prescription_pdf(draft_str, thread_id) or ""
    except Exception:
        pass

    # Pull structured fields out of the draft JSON for the prescriptions table
    diagnosis = ""
    doctor_notes = ""
    medications: list = []
    follow_up_date = ""
    follow_up_instructions = ""
    try:
        draft_obj = json.loads(draft_str) if isinstance(draft_str, str) else (draft if isinstance(draft, dict) else {})
        formal = draft_obj.get("formal_prescription", draft_obj)
        diagnosis = formal.get("diagnosis", "")
        doctor_notes = formal.get("clinical_notes", "")
        follow_up_date = formal.get("follow_up_date", "")
        follow_up_instructions = formal.get("follow_up_instructions", "")
        meds_raw = formal.get("medications", [])
        medications = meds_raw if isinstance(meds_raw, list) else []
    except Exception:
        pass

    # Fetch session to get clinic_id and patient_id
    session = db.get_session(thread_id)
    clinic_id = session.get("clinic_id", "") if session else ""
    patient_id = session.get("patient_id", "") if session else ""
    appointment_type = session.get("appointment_type", "") if session else ""

    # Write structured prescription row
    prescription_id = db.create_prescription(
        thread_id=thread_id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        diagnosis=diagnosis,
        doctor_notes=doctor_notes,
        medications=medications,
        follow_up_date=follow_up_date,
        follow_up_instructions=follow_up_instructions,
        verified=bool(verified),
        pdf_path=pdf_path,
    )

    # Write receipt row
    db.create_receipt(
        thread_id=thread_id,
        clinic_id=clinic_id,
        patient_id=patient_id,
        appointment_type=appointment_type,
        prescription_id=prescription_id,
    )

    # Update session with draft + pdf path
    db.update_session(
        thread_id,
        status="prescription_ready",
        prescription_draft=draft_str,
        prescription_verified=1 if verified else 0,
        prescription_pdf_path=pdf_path,
    )
