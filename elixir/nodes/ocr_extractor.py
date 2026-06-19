"""OCR extractor for image-based document uploads (prescriptions, lab reports, referrals)."""
from __future__ import annotations

import base64
import io
import logging
import os

logger = logging.getLogger("elixire.ocr")

# Allow .env to override the Tesseract binary path (important on Windows)
_TESSERACT_CMD = os.getenv("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def extract_text_from_image(image_bytes: bytes, filename: str = "") -> dict:
    """
    Extract text from an image file using pytesseract.
    Falls back gracefully if pytesseract / Tesseract is not installed.

    Returns: {extracted_text, confidence, method, error}
    """
    try:
        from PIL import Image
        import pytesseract

        # Point to the installed binary (no-op if already on PATH)
        if os.path.exists(_TESSERACT_CMD):
            pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

        image = Image.open(io.BytesIO(image_bytes))
        # Convert to RGB if needed (handles CMYK, RGBA, etc.)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        text = pytesseract.image_to_string(image, lang="eng")
        confidence_data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        confidences = [c for c in confidence_data["conf"] if isinstance(c, int) and c > 0]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        logger.info("OCR extracted %d chars from %s (conf=%.0f%%)", len(text), filename, avg_confidence)
        return {
            "extracted_text": text.strip(),
            "confidence": round(avg_confidence / 100, 2),
            "method": "pytesseract",
            "error": None,
        }

    except ImportError:
        logger.warning("pytesseract/PIL not installed — storing image reference for manual review")
        return {
            "extracted_text": "",
            "confidence": 0.0,
            "method": "unavailable",
            "error": "OCR library not installed — document stored for manual review",
        }
    except Exception as e:
        logger.error("OCR failed for %s: %s", filename, e)
        return {
            "extracted_text": "",
            "confidence": 0.0,
            "method": "failed",
            "error": str(e),
        }


def extract_from_b64(data_b64: str, filename: str, content_type: str = "") -> dict:
    """Decode base64 data and route to PDF or image extractor."""
    try:
        raw_bytes = base64.b64decode(data_b64)
    except Exception as e:
        return {"extracted_text": "", "confidence": 0.0, "method": "failed", "error": f"base64 decode failed: {e}"}

    # Route by content type or filename extension
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    is_pdf = "pdf" in content_type.lower() or ext == "pdf"
    is_image = any(ext == x for x in ("jpg", "jpeg", "png", "heic", "webp", "bmp", "tiff"))

    if is_pdf:
        return _extract_pdf(raw_bytes, filename)
    elif is_image:
        return extract_text_from_image(raw_bytes, filename)
    else:
        # Try image first, fall back to PDF
        result = extract_text_from_image(raw_bytes, filename)
        if not result["extracted_text"]:
            result = _extract_pdf(raw_bytes, filename)
        return result


def _extract_pdf(pdf_bytes: bytes, filename: str) -> dict:
    """Use existing pdf_ingest logic to extract text from PDF bytes."""
    try:
        import pdfminer.high_level as pdfminer_hl
        text = pdfminer_hl.extract_text(io.BytesIO(pdf_bytes))
        return {
            "extracted_text": (text or "").strip(),
            "confidence": 0.95,
            "method": "pdfminer",
            "error": None,
        }
    except Exception as e:
        logger.error("PDF extraction failed for %s: %s", filename, e)
        return {"extracted_text": "", "confidence": 0.0, "method": "failed", "error": str(e)}
