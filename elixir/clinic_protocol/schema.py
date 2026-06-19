"""Pydantic models for clinic protocol configuration."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class DocumentRequirement(BaseModel):
    document_type: str
    label: str
    required: bool = False
    description: Optional[str] = None


class AppointmentType(BaseModel):
    type_id: str
    type_name: str
    required_patient_info: list[str] = Field(
        default=["full_name", "date_of_birth", "contact_number"]
    )
    required_symptom_questions: list[str] = Field(default_factory=list)
    required_documents: list[DocumentRequirement] = Field(default_factory=list)
    required_history_fields: list[str] = Field(
        default=["current_medications", "known_allergies"]
    )
    consultation_form_fields: list[str] = Field(default_factory=list)
    prescription_template: str = "general_standard"


class ClinicProtocol(BaseModel):
    clinic_id: str
    clinic_name: str
    specialty: str
    doctor_name: str
    doctor_qualifications: str = ""
    clinic_address: str = ""
    clinic_phone: str = ""
    appointment_types: list[AppointmentType]

    def get_appointment_type(self, type_id: str) -> Optional[AppointmentType]:
        for apt in self.appointment_types:
            if apt.type_id == type_id:
                return apt
        return None
