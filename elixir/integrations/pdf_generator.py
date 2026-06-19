"""Generate formatted prescription PDFs from Elixire prescription_draft data."""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger("elixire.pdf_generator")

PRESCRIPTIONS_DIR = Path(__file__).resolve().parents[1] / "static" / "prescriptions"


def generate_prescription_pdf(prescription_draft: str, session_id: str) -> str | None:
    """
    Generate a PDF from the prescription_draft JSON.
    Returns the file path relative to static/ if successful, else None.
    Falls back gracefully if reportlab is not installed.
    """
    PRESCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PRESCRIPTIONS_DIR / f"{session_id}.pdf"

    try:
        data = json.loads(prescription_draft) if isinstance(prescription_draft, str) else prescription_draft
        formal = data.get("formal_prescription", {})
        patient_instructions = data.get("patient_instructions", {})
    except (json.JSONDecodeError, AttributeError) as e:
        logger.error("Invalid prescription_draft JSON: %s", e)
        return None

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
        )

        doc = SimpleDocTemplate(
            str(out_path),
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )

        styles = getSampleStyleSheet()
        teal = colors.HexColor("#0F9F7A")
        dark = colors.HexColor("#111827")
        gray = colors.HexColor("#6B7280")

        header_style = ParagraphStyle("header", parent=styles["Heading1"],
                                      textColor=teal, fontSize=18, spaceAfter=4)
        sub_style = ParagraphStyle("sub", parent=styles["Normal"],
                                   textColor=gray, fontSize=10)
        section_style = ParagraphStyle("section", parent=styles["Heading2"],
                                       textColor=dark, fontSize=12, spaceBefore=12, spaceAfter=4)
        body_style = ParagraphStyle("body", parent=styles["Normal"],
                                    textColor=dark, fontSize=10, spaceAfter=4)
        disclaimer_style = ParagraphStyle("disclaimer", parent=styles["Normal"],
                                          textColor=gray, fontSize=8, spaceAfter=4)

        story = []

        # Clinic header
        clinic_name = formal.get("clinic_name", "Clinic")
        doctor_name = formal.get("doctor_name", "Doctor")
        qualifications = formal.get("doctor_qualifications", "")
        story.append(Paragraph(clinic_name, header_style))
        if doctor_name or qualifications:
            story.append(Paragraph(f"{doctor_name} {qualifications}".strip(), sub_style))
        story.append(Spacer(1, 0.3 * cm))
        story.append(HRFlowable(width="100%", thickness=1.5, color=teal))
        story.append(Spacer(1, 0.4 * cm))

        # Patient + date info
        today = formal.get("date", date.today().strftime("%d %B %Y"))
        patient_name = formal.get("patient_name", "")
        patient_dob = formal.get("patient_dob", "")

        info_data = [
            ["Patient:", patient_name, "Date:", today],
            ["Date of Birth:", patient_dob, "", ""],
        ]
        info_table = Table(info_data, colWidths=[3 * cm, 7 * cm, 2 * cm, 5 * cm])
        info_table.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#6B7280")),
            ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#6B7280")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(info_table)
        story.append(Spacer(1, 0.3 * cm))

        # Diagnosis
        diagnosis = formal.get("diagnosis", "")
        if diagnosis:
            story.append(Paragraph("Diagnosis", section_style))
            story.append(Paragraph(diagnosis, body_style))

        # Medications table
        medications = formal.get("medications", [])
        if medications:
            story.append(Paragraph("Medications Prescribed", section_style))
            med_header = [["#", "Medication", "Dosage", "Frequency", "Duration", "Instructions"]]
            med_rows = [
                [
                    str(i + 1),
                    m.get("name", ""),
                    m.get("dosage", ""),
                    m.get("frequency", ""),
                    m.get("duration", ""),
                    m.get("instructions", ""),
                ]
                for i, m in enumerate(medications)
            ]
            med_table = Table(
                med_header + med_rows,
                colWidths=[0.7 * cm, 4 * cm, 2.5 * cm, 3 * cm, 2.5 * cm, 4.3 * cm],
            )
            med_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), teal),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F9FC")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E5E7EB")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(med_table)

        # Follow-up
        follow_up_date = formal.get("follow_up_date", "")
        follow_up_instructions = formal.get("follow_up_instructions", "")
        if follow_up_date or follow_up_instructions:
            story.append(Paragraph("Follow-up", section_style))
            if follow_up_date:
                story.append(Paragraph(f"Next appointment: {follow_up_date}", body_style))
            if follow_up_instructions:
                story.append(Paragraph(follow_up_instructions, body_style))

        # Signature line
        story.append(Spacer(1, 1 * cm))
        story.append(HRFlowable(width="40%", thickness=0.5, color=dark))
        story.append(Paragraph(f"Dr. {doctor_name}", body_style))
        if qualifications:
            story.append(Paragraph(qualifications, sub_style))

        # Disclaimer
        story.append(Spacer(1, 0.5 * cm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#E5E7EB")))
        story.append(Spacer(1, 0.2 * cm))
        story.append(Paragraph(
            "This prescription was generated by Elixire AI. The prescribing doctor has reviewed and authorised this document.",
            disclaimer_style,
        ))

        doc.build(story)
        logger.info("Prescription PDF generated: %s", out_path)
        return f"prescriptions/{session_id}.pdf"

    except ImportError:
        logger.warning("reportlab not installed — generating text prescription instead")
        return _generate_text_fallback(formal, patient_instructions, out_path.with_suffix(".txt"), session_id)
    except Exception as e:
        logger.error("PDF generation failed: %s", e)
        return None


def _generate_text_fallback(formal: dict, patient_instructions: dict, out_path: Path, session_id: str) -> str:
    """Plain text fallback when reportlab is not installed."""
    lines = [
        f"PRESCRIPTION",
        f"============",
        f"Clinic: {formal.get('clinic_name', 'Clinic')}",
        f"Doctor: {formal.get('doctor_name', 'Doctor')} {formal.get('doctor_qualifications', '')}",
        f"Date: {formal.get('date', '')}",
        f"",
        f"Patient: {formal.get('patient_name', '')}",
        f"DOB: {formal.get('patient_dob', '')}",
        f"",
        f"Diagnosis: {formal.get('diagnosis', '')}",
        f"",
        f"MEDICATIONS:",
    ]
    for i, med in enumerate(formal.get("medications", []), 1):
        lines.append(f"{i}. {med.get('name', '')} — {med.get('dosage', '')} — {med.get('frequency', '')} — {med.get('duration', '')}")
        if med.get("instructions"):
            lines.append(f"   Instructions: {med['instructions']}")
    lines.extend([
        f"",
        f"Follow-up: {formal.get('follow_up_date', 'As advised')}",
        f"{formal.get('follow_up_instructions', '')}",
        f"",
        f"Signature: _____________________",
        f"Dr. {formal.get('doctor_name', '')}",
    ])
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return f"prescriptions/{session_id}.txt"
