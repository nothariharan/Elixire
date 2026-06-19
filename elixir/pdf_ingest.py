import io, re
from PyPDF2 import PdfReader
from pdfminer.high_level import extract_text as pdfminer_extract

DATE_PATTERN = re.compile(
    r'\b(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\d{4}-\d{2}-\d{2}|'
    r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4})\b',
    re.IGNORECASE
)
EVENT_KEYWORDS = {
    "diagnosis": ["diagnosed", "diagnosis", "impression", "assessment"],
    "medication": ["prescribed", "medication", "drug", "dosage", "mg", "tablet"],
    "lab_result": ["hemoglobin", "creatinine", "wbc", "rbc", "esr", "crp", "result", "level"],
    "procedure": ["surgery", "procedure", "operation", "biopsy", "scan", "mri", "ct", "x-ray"],
}

def _classify_event(text: str) -> str:
    text_lower = text.lower()
    for event_type, keywords in EVENT_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            return event_type
    return "note"

def parse_patient_pdf(pdf_bytes: bytes) -> list[dict]:
    timeline = []
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for page_num, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if not text.strip() and page_num == 0:
                text = pdfminer_extract(io.BytesIO(pdf_bytes))
            sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) > 20]
            for sentence in sentences:
                date_match = DATE_PATTERN.search(sentence)
                timeline.append({
                    "date": date_match.group(0) if date_match else "unknown",
                    "event_type": _classify_event(sentence),
                    "description": sentence[:300],
                    "source_page": page_num + 1,
                })
    except Exception:
        pass
    return timeline[:30]
