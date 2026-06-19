import json
import urllib.request
import urllib.error
import sys

THREAD_ID = sys.argv[1] if len(sys.argv) > 1 else None
STEP = sys.argv[2] if len(sys.argv) > 2 else "all"


def post(url, data, timeout=180):
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body}


if STEP in ("all", "1"):
    print("=== STEP 1: POST /intake ===")
    status, resp = post(
        "http://localhost:8000/intake",
        {
            "message": "I have a fever of 38.5C and mild nausea. No known allergies.",
            "clinic_id": "demo_cardiology",
            "mode": "fast",
        },
    )
    print(f"HTTP {status}")
    print(f"status field: {resp.get('status')}")
    print(f"questions: {resp.get('questions')}")
    THREAD_ID = resp.get("thread_id")
    print(f"thread_id: {THREAD_ID}")
    if STEP == "1":
        sys.exit(0)

if STEP in ("all", "2") and THREAD_ID:
    print("\n=== STEP 2: POST /respond ===")
    status, resp = post(
        "http://localhost:8000/respond",
        {
            "thread_id": THREAD_ID,
            "answer": "No chronic conditions, no surgeries, no medications. Family history is clear. I don't smoke or drink.",
        },
    )
    print(f"HTTP {status}")
    print(f"status field: {resp.get('status')}")
    if resp.get("questions"):
        print(f"questions (unexpected if complete): {resp['questions']}")
    if resp.get("doctor_brief"):
        brief = resp["doctor_brief"]
        if isinstance(brief, dict):
            print(f"doctor_brief keys: {list(brief.keys())}")
        else:
            print(f"doctor_brief preview: {str(brief)[:300]}")
    print(json.dumps(resp, indent=2)[:4000])
    if STEP == "2":
        sys.exit(0)

if STEP in ("all", "3") and THREAD_ID:
    print("\n=== STEP 3: POST /consultation ===")
    status, resp = post(
        "http://localhost:8000/consultation",
        {
            "thread_id": THREAD_ID,
            "clinic_id": "demo_cardiology",
            "diagnosis": "Viral fever, likely self-limiting",
            "prescribed_medications": [
                {"name": "Paracetamol", "dose": "500mg", "frequency": "TDS", "duration": "3 days"}
            ],
            "follow_up_instructions": "Return if fever persists beyond 3 days",
            "doctor_notes": "Rest and hydration advised",
        },
    )
    print(f"HTTP {status}")
    print(f"status field: {resp.get('status')}")
    print(json.dumps(resp, indent=2)[:4000])
