"""Hindi view, drafting and voice typing through the API (AI faked)."""
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

import api
import drafts
import i18n

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"
H = {"X-Requested-With": "LexiGuard"}

DRAFT_TEXT = ("# Leave and Licence Agreement\n\nThis agreement is made at [City] on [Date].\n\n"
              "## 1. Parties\nRavi Kumar (the Owner) and Meera Shah (the Tenant).\n\n"
              "## 2. Rent and deposit\nRent is **₹20,000** per month. The deposit is ₹40,000.\n- Paid by the 5th\n\n"
              "## 3. Lock-in\nThe Tenant shall not vacate during the first six months. If the Tenant leaves before the lock-in period ends, the entire security deposit shall be forfeited.\n\n"
              "## 4. Termination\nEither party may terminate this agreement with one month's notice in writing. "
              "This agreement shall be governed by the laws of India and the courts at [City] shall have jurisdiction.\n\n"
              "Signature: ____________\n\n---NOTES---\n- Stamp duty depends on your state.")


@pytest.fixture(scope="module")
def client():
    c = TestClient(api.app)
    assert c.post("/api/auth/signup", json={"name": "Dee", "email": "dee@example.com", "password": "secret123"}, headers=H).status_code == 200
    return c


@pytest.fixture
def ai(monkeypatch):
    for mod in (drafts, i18n):
        monkeypatch.setattr(mod, "llm_available", lambda: True)


def test_draft_types(client):
    types = client.get("/api/draft-types").json()
    keys = [t["value"] for t in types]
    assert "rental" in keys and "legal_notice" in keys and all(t["fields"] for t in types)


def test_questions_then_draft_then_revise_then_export(client, ai, monkeypatch):
    monkeypatch.setattr(drafts, "chat_completion", lambda *a, **k: json.dumps(
        {"title": "Flat rent agreement", "questions": [{"question": "What is the property address?", "hint": "Flat 4B"}]}))
    q = client.post("/api/drafts/questions", json={"doc_type": "rental", "description": "Flat for Meera, rent 20k", "fields": {}}, headers=H)
    assert q.status_code == 200 and q.json()["questions"][0]["id"] == "q1"

    seen = {}
    def fake_stream(messages, **kw):
        seen["system"], seen["user"] = messages[0]["content"], messages[1]["content"]
        for i in range(0, len(DRAFT_TEXT), 40):
            yield DRAFT_TEXT[i:i + 40]
    monkeypatch.setattr(drafts, "stream_chat", fake_stream)
    r = client.post("/api/drafts", json={"doc_type": "rental", "description": "Flat for Meera, rent 20k",
                                         "fields": {"tenant": "Meera Shah"}, "favour": "tenant", "language": "en",
                                         "answers": [{"question": "Address?", "answer": "Flat 4B, Pune"}]}, headers=H)
    assert r.status_code == 200 and r.text == DRAFT_TEXT
    did = r.headers["x-draft-id"]
    assert "protect the tenant" in seen["system"] and "Meera Shah" in seen["user"] and "Flat 4B, Pune" in seen["user"]
    d = client.get(f"/api/drafts/{did}").json()
    assert d["content"].startswith("# Leave and Licence") and "---NOTES---" not in d["content"]
    assert "Stamp duty" in d["notes"] and d["title"] == "Leave and Licence Agreement"

    revised = DRAFT_TEXT.split("---NOTES---")[0].replace("one month's notice", "two months' notice") + "\n---CHANGES---\nNotice changed to two months."
    monkeypatch.setattr(drafts, "stream_chat", lambda m, **k: iter([revised]))
    assert client.post(f"/api/drafts/{did}/revise", json={"instruction": "two months notice"}, headers=H).status_code == 200
    d = client.get(f"/api/drafts/{did}").json()
    assert "two months' notice" in d["content"] and "---CHANGES---" not in d["content"] and len(d["versions"]) == 1
    restored = client.post(f"/api/drafts/{did}/restore/0", headers=H).json()
    assert "one month's notice" in restored["content"]

    assert client.put(f"/api/drafts/{did}", json={"title": "My flat agreement"}, headers=H).json()["ok"]
    assert client.get("/api/drafts").json()[0]["title"] == "My flat agreement"
    docx = client.get(f"/api/drafts/{did}/export?format=docx")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    pdf = client.get(f"/api/drafts/{did}/export?format=pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    chk = client.post(f"/api/drafts/{did}/check", headers=H)
    assert chk.status_code == 200, chk.text
    doc = client.get(f"/api/contracts/{chk.json()['contract_id']}").json()
    assert doc["document_type"] == "rental" and doc["perspective"] == "tenant"
    assert client.delete(f"/api/drafts/{did}", headers=H).json()["ok"]


def test_drafts_are_private(client, ai, monkeypatch):
    monkeypatch.setattr(drafts, "stream_chat", lambda m, **k: iter([DRAFT_TEXT]))
    did = client.post("/api/drafts", json={"doc_type": "nda", "description": "NDA"}, headers=H).headers["x-draft-id"]
    other = TestClient(api.app)
    other.post("/api/auth/signup", json={"name": "Eve", "email": "eve@example.com", "password": "secret123"}, headers=H)
    assert other.get(f"/api/drafts/{did}").status_code == 404
    assert other.get("/api/drafts").json() == []


def test_empty_request_and_no_ai(client, monkeypatch):
    monkeypatch.setattr(drafts, "llm_available", lambda: True)
    assert client.post("/api/drafts", json={"doc_type": "nda"}, headers=H).status_code == 400
    monkeypatch.setattr(drafts, "llm_available", lambda: False)
    r = client.post("/api/drafts", json={"doc_type": "nda", "description": "x"}, headers=H)
    assert r.status_code == 503 and "GROQ_API_KEY" in r.json()["detail"]


def test_voice_typing(client, monkeypatch):
    monkeypatch.setattr(drafts, "transcribe", lambda audio, name, lang, prompt=None: f"heard {len(audio)} bytes in {lang}")
    r = client.post("/api/transcribe", files={"file": ("speech.webm", b"\x1a" * 500, "audio/webm")}, data={"language": "hi"}, headers=H)
    assert r.status_code == 200 and r.json()["text"] == "heard 500 bytes in hi"


def test_hindi_view_translates_once_and_caches(client, ai, monkeypatch):
    with open(SAMPLES / "offer_letter_sample.txt", "rb") as fh:
        cid = client.post("/api/analyze", files={"file": ("offer.txt", fh)}, headers=H).json()["contract_id"]
    calls = []
    def fake(messages, **kw):
        payload = json.loads(messages[1]["content"].split("\n", 1)[1])
        calls.append(payload)
        if "summary" in payload:
            return json.dumps({"summary": "हिंदी सारांश"})
        return json.dumps({"items": [{**it, "plain_english_explanation": "हिंदी व्याख्या " + it["id"][:4]} for it in payload["items"]]})
    monkeypatch.setattr(i18n, "chat_completion", fake)
    r = client.post(f"/api/contracts/{cid}/translate", json={"lang": "hi"}, headers=H).json()
    assert r["complete"] and r["summary"] == "हिंदी सारांश" and len(r["flags"]) == 11
    assert all(len(c.get("items", [])) <= 4 for c in calls)
    n = len(calls)
    again = client.post(f"/api/contracts/{cid}/translate", json={"lang": "hi"}, headers=H).json()
    assert len(calls) == n and again["flags"] == r["flags"]   # served from cache


def test_tts_speaks_hindi_text_without_translating(client, monkeypatch):
    import tts
    monkeypatch.setattr(tts, "translate_to_hindi", lambda t: (_ for _ in ()).throw(AssertionError("should not translate")))
    r = client.post("/api/tts", json={"text": "आपको तीन महीने का नोटिस देना होगा", "lang": "hi"}, headers=H)
    assert r.status_code == 503 and r.json()["lang"] == "hi" and r.json()["fallback_text"].startswith("आपको")
