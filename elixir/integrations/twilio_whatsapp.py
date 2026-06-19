"""Send prescription via WhatsApp using Twilio. Falls back to QR code if not configured."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("elixire.whatsapp")


def send_prescription(
    patient_contact: str,
    pdf_path: str,
    patient_name: str,
    clinic_name: str = "the clinic",
    base_url: str = "",
) -> dict:
    """
    Send prescription PDF to patient via WhatsApp.
    Returns: {sent: bool, method: str, message: str, qr_code_url: str | None}
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    from_number = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")  # Twilio sandbox default

    if not account_sid or not auth_token:
        logger.info("Twilio not configured — generating QR code fallback")
        return _qr_fallback(pdf_path, patient_name, base_url)

    if not patient_contact:
        return {"sent": False, "method": "none", "message": "No patient contact number provided", "qr_code_url": None}

    contact_e164 = _normalize_phone(patient_contact)
    if not contact_e164:
        return {"sent": False, "method": "none", "message": f"Invalid phone number: {patient_contact}", "qr_code_url": None}

    try:
        from twilio.rest import Client
        client = Client(account_sid, auth_token)

        pdf_url = f"{base_url.rstrip('/')}/static/{pdf_path}" if base_url and pdf_path else ""
        body_text = (
            f"Hello {patient_name},\n\n"
            f"Your prescription from {clinic_name} is ready.\n"
            f"Please find it attached or download it here:\n{pdf_url}\n\n"
            f"Thank you for visiting. Stay healthy!"
        )

        msg_kwargs = {
            "from_": from_number,
            "body": body_text,
            "to": f"whatsapp:{contact_e164}",
        }
        if pdf_url:
            msg_kwargs["media_url"] = [pdf_url]

        message = client.messages.create(**msg_kwargs)
        logger.info("WhatsApp sent to %s — SID: %s", contact_e164, message.sid)
        return {
            "sent": True,
            "method": "whatsapp",
            "message": f"Prescription sent to {contact_e164}",
            "qr_code_url": None,
            "message_sid": message.sid,
        }

    except ImportError:
        logger.warning("twilio package not installed — falling back to QR")
        return _qr_fallback(pdf_path, patient_name, base_url)
    except Exception as e:
        logger.error("WhatsApp send failed: %s", e)
        return _qr_fallback(pdf_path, patient_name, base_url)


def _normalize_phone(number: str) -> str | None:
    """Normalize to E.164 format. Handles Indian numbers (+91) and US numbers."""
    import re
    digits = re.sub(r"[^\d+]", "", number)
    if digits.startswith("+"):
        return digits if len(digits) >= 8 else None
    if len(digits) == 10:
        return f"+91{digits}"  # Default to India for demo
    if len(digits) == 11 and digits.startswith("0"):
        return f"+91{digits[1:]}"
    return None


def _qr_fallback(pdf_path: str, patient_name: str, base_url: str) -> dict:
    """Generate a QR code URL pointing to the prescription download."""
    if not pdf_path or not base_url:
        return {
            "sent": False,
            "method": "qr_unavailable",
            "message": "Prescription ready for download from the dashboard",
            "qr_code_url": None,
        }

    download_url = f"{base_url.rstrip('/')}/static/{pdf_path}"
    # Use a free QR code API for demo
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={download_url}"
    return {
        "sent": False,
        "method": "qr_code",
        "message": f"Prescription ready — show QR code to {patient_name}",
        "qr_code_url": qr_url,
        "download_url": download_url,
    }
