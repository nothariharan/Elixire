from typing import TypedDict, Optional, Literal, Any


class SubQuery(TypedDict):
    search_query: str
    ranking_query: str
    mesh_terms: list[str]


class SourceSnippet(TypedDict):
    source_id: str
    source_name: str
    url: str
    text: str
    entity_fingerprint: set[str]


class ConditionScore(TypedDict):
    condition: str
    probability: float
    source: str


class PatientHistoryEvent(TypedDict):
    date: str
    event_type: str
    description: str
    source_page: int


# ── new v3.0: source tier classification ──────────────────────────────────────
class SourceTier(TypedDict):
    tier: Literal["canonical", "literature", "contextual"]
    # canonical   = umls/snomed/rxnorm/dailymed — can ground confidence scores
    # literature  = europe pmc, pubmed, semantic scholar, openalex, clinicaltrials.gov — evidence, not ground truth
    # contextual  = reddit, wikipedia, general web — informs follow-up questions only, never cited in confidence_spread


# ── new v3.0: provenance record for audit trail ──────────────────────────────
class ProvenanceRecord(TypedDict):
    claim: str                  # the specific claim text
    source_id: str              # matches SourceSnippet.source_id
    source_tier: str            # "canonical" | "literature"
    matched_sentence: str       # exact sentence from source that supports the claim
    canonical_code: Optional[str]  # UMLS CUI / SNOMED code if applicable
    verified_by: str            # which model/provider verified this (for audit)


class ElixirState(TypedDict):
    raw_input: str
    mode: Literal["fast", "deep"]
    thread_id: str
    locale: str

    patient_history_timeline: list[PatientHistoryEvent]
    follow_up_questions: list[str]
    patient_responses: list[str]
    follow_up_count: int

    is_valid: bool

    standardized_symptoms: list[str]
    mesh_terms: list[str]
    subqueries: list[SubQuery]
    severity: Literal["low", "medium", "high"]
    triage_confidence: float
    triage_matched_disease: Optional[str]

    raw_snippets: list[SourceSnippet]
    clustered_snippets: list[SourceSnippet]
    sources_checked: int
    rate_limited_sources: set[str]

    synthesis_draft: str
    confidence_spread: list[ConditionScore]

    verified: bool
    final_confidence_spread: list[ConditionScore]

    action_plan_draft: str

    latency_ms: int
    error: Optional[str]
    sse_log: list[str]

    # ── new v3.0: sponsor routing / audit ─────────────────────────────────
    model_provider_log: list[dict]
    # e.g. [{"node": "triage", "provider": "featherless", "model": "...", "tokens": 412}]
    # populated by every llm node. drives the sse ui's "powered by" indicators.

    # ── new v3.0: emergency gate (node 0.5) ───────────────────────────────
    emergency_flag: bool
    emergency_reason: Optional[str]
    # set by deterministic emergency node before triage. if true, graph short-circuits
    # to end with an emergency-care message. no llm call involved.

    # ── new v3.0: provenance ──────────────────────────────────────────────
    provenance: list[ProvenanceRecord]
    # populated by verification node. every surviving claim in final_confidence_spread
    # and every claim in action_plan_draft must have a corresponding provenancerecord.

    # ── new v3.0: canonical grounding ─────────────────────────────────────
    canonical_terms: dict[str, str]
    # maps standardized_symptoms / conditions -> umls cui or snomed code, populated
    # during triage node via umls uts api (or empty dict if no key).


# ── Elixire v1.0: clinic operating system state ───────────────────────────────

class ElixireState(TypedDict):
    # ── Session ──────────────────────────────────────────────────────────────
    session_id: str
    clinic_id: str
    clinic_protocol: dict           # loaded ClinicProtocol as dict at session start
    appointment_type: str
    patient_id: str

    # ── Patient identity ──────────────────────────────────────────────────────
    patient_name: str
    patient_dob: str
    patient_contact: str

    # ── Routing & validation (preserved from ElixirState) ────────────────────
    raw_input: str
    locale: str
    is_valid: bool
    emergency_flag: bool
    emergency_reason: Optional[str]

    # ── HITL state (preserved from ElixirState) ───────────────────────────────
    patient_history_timeline: list[PatientHistoryEvent]
    follow_up_questions: list[str]
    patient_responses: list[str]
    follow_up_count: int

    # ── Intake outputs ────────────────────────────────────────────────────────
    chief_complaint: str
    symptom_timeline: list[dict]
    medical_history: dict
    current_medications: list[str]
    allergies: list[str]

    # ── Document state ────────────────────────────────────────────────────────
    uploaded_documents: list[dict]      # [{filename, content_type, data_b64}]
    extracted_document_data: list[dict] # [{filename, extracted_text, doc_type, key_values}]
    missing_required_documents: list[str]

    # ── Brief state ───────────────────────────────────────────────────────────
    doctor_brief: str                   # structured markdown for the doctor dashboard

    # ── Post-consultation state ───────────────────────────────────────────────
    doctor_notes: str
    diagnosis: str
    prescribed_medications: list[dict]  # [{name, dosage, frequency, duration, instructions}]
    follow_up_date: str
    follow_up_instructions: str

    # ── Prescription state ────────────────────────────────────────────────────
    prescription_draft: str             # JSON with formal + patient-friendly versions
    prescription_verified: bool
    prescription_pdf_path: str

    # ── Telemetry (preserved from ElixirState) ────────────────────────────────
    sse_log: list[str]
    model_provider_log: list[dict]
    provenance: list[ProvenanceRecord]
    error: Optional[str]
    latency_ms: int
