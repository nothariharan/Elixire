"""SQLite persistence for Elixire — clinics, patients, sessions, prescriptions, receipts."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "elixire.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.utcnow().isoformat()


# ── schema ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    with _conn() as c:
        # clinics — one row per doctor/clinic setup
        c.execute("""
            CREATE TABLE IF NOT EXISTS clinics (
                clinic_id              TEXT PRIMARY KEY,
                clinic_name            TEXT,
                specialty              TEXT,
                doctor_name            TEXT,
                doctor_qualifications  TEXT,
                clinic_address         TEXT,
                clinic_phone           TEXT,
                created_at             TEXT
            )
        """)

        # patients — one row per unique patient (deduped by clinic+name+dob)
        c.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                patient_id     TEXT PRIMARY KEY,
                clinic_id      TEXT,
                full_name      TEXT,
                date_of_birth  TEXT,
                contact        TEXT,
                allergies      TEXT DEFAULT '[]',
                created_at     TEXT,
                updated_at     TEXT,
                UNIQUE(clinic_id, full_name, date_of_birth)
            )
        """)

        # sessions — one row per clinic visit / thread
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                thread_id              TEXT PRIMARY KEY,
                clinic_id              TEXT,
                patient_id             TEXT,
                patient_name           TEXT,
                patient_dob            TEXT,
                patient_contact        TEXT,
                appointment_type       TEXT,
                status                 TEXT DEFAULT 'waiting',
                chat_log               TEXT DEFAULT '[]',
                doctor_brief           TEXT,
                diagnosis              TEXT,
                prescription_draft     TEXT,
                prescription_verified  INTEGER DEFAULT 0,
                prescription_pdf_path  TEXT,
                created_at             TEXT,
                updated_at             TEXT
            )
        """)

        # prescriptions — structured, one row per completed consultation
        c.execute("""
            CREATE TABLE IF NOT EXISTS prescriptions (
                prescription_id        TEXT PRIMARY KEY,
                thread_id              TEXT,
                clinic_id              TEXT,
                patient_id             TEXT,
                issued_date            TEXT,
                diagnosis              TEXT,
                doctor_notes           TEXT,
                medications            TEXT DEFAULT '[]',
                follow_up_date         TEXT,
                follow_up_instructions TEXT,
                verified               INTEGER DEFAULT 0,
                pdf_path               TEXT,
                created_at             TEXT
            )
        """)

        # receipts — one row per completed visit (audit trail)
        c.execute("""
            CREATE TABLE IF NOT EXISTS receipts (
                receipt_id         TEXT PRIMARY KEY,
                thread_id          TEXT,
                clinic_id          TEXT,
                patient_id         TEXT,
                visit_date         TEXT,
                appointment_type   TEXT,
                prescription_id    TEXT,
                consultation_fee   REAL DEFAULT 0.0,
                notes              TEXT,
                created_at         TEXT
            )
        """)

        c.commit()

    # safe migrations for existing databases
    _migrate()


def _migrate() -> None:
    """Add columns / indexes that didn't exist in earlier versions of the schema."""
    col_migrations = [
        ("clinics",  "doctor_qualifications TEXT"),
        ("clinics",  "clinic_address TEXT"),
        ("clinics",  "clinic_phone TEXT"),
        ("sessions", "patient_id TEXT"),
        ("sessions", "prescription_pdf_path TEXT"),
    ]
    with _conn() as c:
        for table, col_def in col_migrations:
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                c.commit()
            except sqlite3.OperationalError:
                pass  # column already exists

        # Retrofit the UNIQUE constraint on patients (can't ALTER TABLE in SQLite)
        try:
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_patients_identity "
                "ON patients(clinic_id, full_name, date_of_birth)"
            )
            c.commit()
        except sqlite3.OperationalError:
            pass


# ── clinics ───────────────────────────────────────────────────────────────────

def upsert_clinic(
    clinic_id: str,
    clinic_name: str,
    specialty: str,
    doctor_name: str,
    doctor_qualifications: str = "",
    clinic_address: str = "",
    clinic_phone: str = "",
) -> None:
    with _conn() as c:
        c.execute(
            """
            INSERT INTO clinics
                (clinic_id, clinic_name, specialty, doctor_name,
                 doctor_qualifications, clinic_address, clinic_phone, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(clinic_id) DO UPDATE SET
                clinic_name           = excluded.clinic_name,
                specialty             = excluded.specialty,
                doctor_name           = excluded.doctor_name,
                doctor_qualifications = excluded.doctor_qualifications,
                clinic_address        = excluded.clinic_address,
                clinic_phone          = excluded.clinic_phone
            """,
            (clinic_id, clinic_name, specialty, doctor_name,
             doctor_qualifications, clinic_address, clinic_phone, _now()),
        )
        c.commit()


def get_clinic(clinic_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM clinics WHERE clinic_id = ?", (clinic_id,)).fetchone()
        return dict(row) if row else None


def list_clinics() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM clinics ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


# ── patients ──────────────────────────────────────────────────────────────────

def upsert_patient(
    clinic_id: str,
    full_name: str,
    date_of_birth: str,
    contact: str = "",
    allergies: list | None = None,
) -> str:
    """Return patient_id, creating a new record only if this person hasn't visited before.
    Uses INSERT OR IGNORE + UPDATE so the UNIQUE(clinic_id, full_name, dob) constraint
    prevents duplicate rows even under concurrent requests."""
    patient_id = str(uuid.uuid4())
    now = _now()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO patients
                (patient_id, clinic_id, full_name, date_of_birth, contact, allergies, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(clinic_id, full_name, date_of_birth) DO UPDATE SET
                contact    = excluded.contact,
                updated_at = excluded.updated_at
            """,
            (patient_id, clinic_id, full_name, date_of_birth,
             contact or "", json.dumps(allergies or []), now, now),
        )
        # Fetch the actual patient_id (may differ if the row already existed)
        row = c.execute(
            "SELECT patient_id FROM patients WHERE clinic_id = ? AND full_name = ? AND date_of_birth = ?",
            (clinic_id, full_name, date_of_birth),
        ).fetchone()
        c.commit()
    return row["patient_id"] if row else patient_id


def get_patient(patient_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
        return dict(row) if row else None


def list_patients(clinic_id: str | None = None) -> list[dict]:
    with _conn() as c:
        if clinic_id:
            rows = c.execute(
                "SELECT * FROM patients WHERE clinic_id = ? ORDER BY created_at DESC",
                (clinic_id,),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM patients ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


# ── sessions ──────────────────────────────────────────────────────────────────

def create_session(
    thread_id: str,
    clinic_id: str,
    patient_name: str,
    patient_dob: str,
    patient_contact: str,
    appointment_type: str,
    patient_id: str = "",
) -> None:
    now = _now()
    with _conn() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO sessions
            (thread_id, clinic_id, patient_id, patient_name, patient_dob, patient_contact,
             appointment_type, status, chat_log, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'intake', '[]', ?, ?)
            """,
            (thread_id, clinic_id, patient_id, patient_name, patient_dob,
             patient_contact, appointment_type, now, now),
        )
        c.commit()


def update_session(thread_id: str, **kwargs) -> None:
    if not kwargs:
        return
    kwargs["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [thread_id]
    with _conn() as c:
        c.execute(f"UPDATE sessions SET {sets} WHERE thread_id = ?", vals)
        c.commit()


def append_message(thread_id: str, role: str, text: str, agent: str = "") -> None:
    """Atomically append a message to chat_log using JSON functions (no read-modify-write race)."""
    entry = json.dumps({"role": role, "text": text, "agent": agent, "ts": _now()})
    now = _now()
    with _conn() as c:
        # json_insert on the last element of the array is atomic within SQLite's serialized writes.
        # Falls back gracefully: if the session row doesn't exist, UPDATE is a no-op.
        c.execute(
            """
            UPDATE sessions
            SET chat_log  = json_insert(COALESCE(chat_log, '[]'), '$[#]', json(?)),
                updated_at = ?
            WHERE thread_id = ?
            """,
            (entry, now, thread_id),
        )
        c.commit()


def get_session(thread_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM sessions WHERE thread_id = ?", (thread_id,)).fetchone()
        return dict(row) if row else None


def list_sessions_by_patient(patient_id: str) -> list[dict]:
    """Return all sessions for a patient, no arbitrary limit."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM sessions WHERE patient_id = ? ORDER BY created_at DESC",
            (patient_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_sessions(clinic_id: str | None = None, limit: int = 50) -> list[dict]:
    with _conn() as c:
        if clinic_id:
            rows = c.execute(
                "SELECT * FROM sessions WHERE clinic_id = ? ORDER BY created_at DESC LIMIT ?",
                (clinic_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ── prescriptions ─────────────────────────────────────────────────────────────

def create_prescription(
    thread_id: str,
    clinic_id: str,
    patient_id: str,
    diagnosis: str,
    doctor_notes: str,
    medications: list,
    follow_up_date: str,
    follow_up_instructions: str,
    verified: bool,
    pdf_path: str = "",
) -> str:
    prescription_id = str(uuid.uuid4())
    now = _now()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO prescriptions
                (prescription_id, thread_id, clinic_id, patient_id, issued_date,
                 diagnosis, doctor_notes, medications, follow_up_date,
                 follow_up_instructions, verified, pdf_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (prescription_id, thread_id, clinic_id, patient_id, now,
             diagnosis, doctor_notes, json.dumps(medications),
             follow_up_date, follow_up_instructions,
             1 if verified else 0, pdf_path, now),
        )
        c.commit()
    return prescription_id


def get_prescription(prescription_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM prescriptions WHERE prescription_id = ?", (prescription_id,)
        ).fetchone()
        return dict(row) if row else None


def get_prescription_by_session(thread_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM prescriptions WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None


def list_prescriptions(clinic_id: str | None = None, patient_id: str | None = None) -> list[dict]:
    with _conn() as c:
        if patient_id:
            rows = c.execute(
                "SELECT * FROM prescriptions WHERE patient_id = ? ORDER BY created_at DESC",
                (patient_id,),
            ).fetchall()
        elif clinic_id:
            rows = c.execute(
                "SELECT * FROM prescriptions WHERE clinic_id = ? ORDER BY created_at DESC",
                (clinic_id,),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM prescriptions ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


# ── receipts ──────────────────────────────────────────────────────────────────

def create_receipt(
    thread_id: str,
    clinic_id: str,
    patient_id: str,
    appointment_type: str,
    prescription_id: str = "",
    consultation_fee: float = 0.0,
    notes: str = "",
) -> str:
    receipt_id = str(uuid.uuid4())
    now = _now()
    with _conn() as c:
        c.execute(
            """
            INSERT INTO receipts
                (receipt_id, thread_id, clinic_id, patient_id, visit_date,
                 appointment_type, prescription_id, consultation_fee, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (receipt_id, thread_id, clinic_id, patient_id, now,
             appointment_type, prescription_id, consultation_fee, notes, now),
        )
        c.commit()
    return receipt_id


def get_receipt(receipt_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM receipts WHERE receipt_id = ?", (receipt_id,)
        ).fetchone()
        return dict(row) if row else None


def get_receipt_by_session(thread_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM receipts WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
        return dict(row) if row else None


def list_receipts(clinic_id: str | None = None, patient_id: str | None = None) -> list[dict]:
    with _conn() as c:
        if patient_id:
            rows = c.execute(
                "SELECT * FROM receipts WHERE patient_id = ? ORDER BY created_at DESC",
                (patient_id,),
            ).fetchall()
        elif clinic_id:
            rows = c.execute(
                "SELECT * FROM receipts WHERE clinic_id = ? ORDER BY created_at DESC",
                (clinic_id,),
            ).fetchall()
        else:
            rows = c.execute("SELECT * FROM receipts ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
