"""Node 2 — Document Processor. Parallel processing of uploaded patient documents.
Reuses ThreadPoolExecutor pattern from research node.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from nodes.ocr_extractor import extract_from_b64
from utils.mmr import mmr_select

MAX_WORKERS = 6
TIMEOUT = 15


def document_processor_node(state: dict) -> dict:
    uploaded = state.get("uploaded_documents", [])

    if not uploaded:
        state["extracted_document_data"] = state.get("extracted_document_data", [])
        state["sse_log"] = state.get("sse_log", []) + ["[docs] no documents uploaded"]
        return state

    results: list[dict] = []
    lock = threading.Lock()

    def process_doc(doc: dict) -> dict:
        filename = doc.get("filename", "document")
        content_type = doc.get("content_type", "")
        data_b64 = doc.get("data_b64", "")

        if not data_b64:
            return {
                "filename": filename,
                "doc_type": _classify_doc_type(filename),
                "extracted_text": "",
                "key_values": {},
                "error": "No data provided",
            }

        extraction = extract_from_b64(data_b64, filename, content_type)
        doc_type = _classify_doc_type(filename, extraction.get("extracted_text", ""))
        key_values = _extract_key_values(extraction.get("extracted_text", ""), doc_type)

        return {
            "filename": filename,
            "doc_type": doc_type,
            "extracted_text": extraction["extracted_text"],
            "extraction_method": extraction["method"],
            "extraction_confidence": extraction["confidence"],
            "key_values": key_values,
            "error": extraction.get("error"),
        }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(process_doc, doc): doc for doc in uploaded}
        for future in as_completed(futures, timeout=TIMEOUT):
            try:
                result = future.result()
                with lock:
                    results.append(result)
            except Exception as e:
                doc = futures[future]
                with lock:
                    results.append({
                        "filename": doc.get("filename", "unknown"),
                        "doc_type": "unknown",
                        "extracted_text": "",
                        "key_values": {},
                        "error": str(e),
                    })

    # Merge with any previously extracted documents (from earlier HITL rounds)
    existing = {d["filename"]: d for d in state.get("extracted_document_data", [])}
    for r in results:
        existing[r["filename"]] = r

    state["extracted_document_data"] = list(existing.values())

    # Update missing_required_documents based on what was just processed
    extracted_types = {d["doc_type"] for d in state["extracted_document_data"] if not d.get("error")}
    missing = state.get("missing_required_documents", [])
    still_missing = [m for m in missing if m not in extracted_types]
    state["missing_required_documents"] = still_missing

    log = state.get("sse_log", [])
    processed = len([r for r in results if not r.get("error")])
    log.append(f"[docs] {processed}/{len(uploaded)} documents processed · types: {', '.join(extracted_types) or 'none'}")
    state["sse_log"] = log
    return state


def _classify_doc_type(filename: str, text: str = "") -> str:
    """Infer document type from filename and content."""
    name_lower = filename.lower()
    text_lower = text.lower()

    if any(k in name_lower or k in text_lower for k in ("prescription", "rx", "presc")):
        return "prescription"
    if any(k in name_lower or k in text_lower for k in ("lab", "report", "blood", "urine", "test result")):
        return "lab_report"
    if any(k in name_lower or k in text_lower for k in ("discharge", "summary", "hospital")):
        return "discharge_summary"
    if any(k in name_lower or k in text_lower for k in ("referral", "refer")):
        return "referral"
    if any(k in name_lower or k in text_lower for k in ("xray", "x-ray", "ct scan", "mri", "ultrasound", "scan")):
        return "imaging_report"
    if any(k in name_lower or k in text_lower for k in ("insurance", "policy", "card")):
        return "insurance"
    return "medical_document"


def _extract_key_values(text: str, doc_type: str) -> dict:
    """
    Extract structured key-value pairs from text based on document type.
    Simple pattern matching — no LLM required.
    """
    import re
    kv = {}

    if doc_type == "prescription":
        # Eye prescription: SPH, CYL, AXIS
        for eye, label in [("RE", "right_eye"), ("LE", "left_eye"), ("OD", "right_eye"), ("OS", "left_eye")]:
            pattern = rf"{eye}[:\s]+SPH\s*([-+]?\d+\.?\d*)\s*CYL\s*([-+]?\d+\.?\d*)\s*AXIS\s*(\d+)"
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                kv[label] = {"sph": m.group(1), "cyl": m.group(2), "axis": m.group(3)}

        # Date
        date_m = re.search(r"Date[:\s]+([A-Za-z0-9\s,/-]+\d{4})", text, re.IGNORECASE)
        if date_m:
            kv["date"] = date_m.group(1).strip()

        # Doctor name
        dr_m = re.search(r"Dr\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)", text)
        if dr_m:
            kv["prescribing_doctor"] = dr_m.group(1)

    elif doc_type == "lab_report":
        # Common lab values
        for test in ["HbA1c", "Hemoglobin", "WBC", "Platelets", "Creatinine", "Glucose"]:
            m = re.search(rf"{test}[:\s]+([\d.]+)\s*([a-zA-Z%/]+)?", text, re.IGNORECASE)
            if m:
                kv[test.lower()] = {"value": m.group(1), "unit": (m.group(2) or "").strip()}

    return kv
