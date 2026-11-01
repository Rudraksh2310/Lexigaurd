"""End-to-end API tests: accounts, privacy between users, analysis,
reports, chat and voice. Run from backend/:  python -m pytest tests -q"""
import pathlib

import pytest
from fastapi.testclient import TestClient

import api
import chat

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "samples"
H = {"X-Requested-With": "LexiGuard"}


def new_client(name, email, password="secret123"):
    c = TestClient(api.app)
    r = c.post("/api/auth/signup", json={"name": name, "email": email, "password": password, "role": "employee"}, headers=H)
    assert r.status_code == 200, r.text
    return c


def upload(c, name, **form):
    with open(SAMPLES / name, "rb") as fh:
        return c.post("/api/analyze", files={"file": (name, fh, "text/plain")}, data=form, headers=H)


@pytest.fixture(scope="module")
def alice():
    return new_client("Alice", "alice@example.com")


@pytest.fixture(scope="module")
def bob():
    return new_client("Bob", "bob@example.com")


def test_signup_validation_and_duplicates():
    c = TestClient(api.app)
    bad = c.post("/api/auth/signup", json={"name": "X", "email": "not-an-email", "password": "secret123"}, headers=H)
    assert bad.status_code == 400 and bad.json()["detail"]["field"] == "email"
    weak = c.post("/api/auth/signup", json={"name": "X", "email": "x@example.com", "password": "short"}, headers=H)
    assert weak.status_code == 400 and weak.json()["detail"]["field"] == "password"
    new_client("Dup", "dup@example.com")
    again = c.post("/api/auth/signup", json={"name": "D", "email": "DUP@example.com", "password": "secret123"}, headers=H)
    assert again.status_code == 409


def test_login_logout_and_me():
    new_client("Carol", "carol@example.com")
    c = TestClient(api.app)
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/login", json={"email": "carol@example.com", "password": "wrong999"}, headers=H).status_code == 401
    r = c.post("/api/auth/login", json={"email": "Carol@Example.com", "password": "secret123", "remember": True}, headers=H)
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/api/auth/me").json()["email"] == "carol@example.com"
    c.post("/api/auth/logout", headers=H)
    assert c.get("/api/auth/me").status_code == 401


def test_csrf_header_required(alice):
    r = alice.post("/api/auth/logout")  # no X-Requested-With
    assert r.status_code == 403


def test_analyze_requires_sign_in():
    with open(SAMPLES / "offer_letter_sample.txt", "rb") as fh:
        r = TestClient(api.app).post("/api/analyze", files={"file": ("a.txt", fh)}, headers=H)
    assert r.status_code == 401


def test_analyze_view_report_and_privacy(alice, bob):
    r = upload(alice, "offer_letter_sample.txt", doc_type="auto")
    assert r.status_code == 200, r.text
    cid = r.json()["contract_id"]
    assert r.json()["analysis"]["document_type"] == "employment"

    detail = alice.get(f"/api/contracts/{cid}").json()
    assert detail["document_text"] and all(
        detail["document_text"][f["start"]:f["end"]] == f["clause_text"] for f in detail["flags"])
    assert [c["id"] for c in alice.get("/api/contracts").json()] == [cid]
    assert alice.get("/api/stats").json()["total_contracts"] == 1

    # Bob can't see, download, chat with, or delete Alice's document
    assert bob.get("/api/contracts").json() == []
    assert bob.get(f"/api/contracts/{cid}").status_code == 404
    assert bob.get(f"/api/contracts/{cid}/report?format=pdf").status_code == 404
    assert bob.get(f"/api/contracts/{cid}/chat").status_code == 404
    assert bob.delete(f"/api/contracts/{cid}", headers=H).status_code == 404
    assert bob.get("/api/stats").json()["total_contracts"] == 0

    pdf = alice.get(f"/api/contracts/{cid}/report?format=pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    docx = alice.get(f"/api/contracts/{cid}/report?format=docx")
    assert docx.status_code == 200 and docx.content[:2] == b"PK"


def test_resume_rejected(alice):
    r = upload(alice, "resume_sample.txt")
    assert r.status_code == 422 and r.json()["detail"]["can_override"] is False


def test_chat_without_ai_key_explains(alice):
    cid = alice.get("/api/contracts").json()[0]["id"]
    assert alice.get(f"/api/contracts/{cid}/chat").json()["available"] is False
    r = alice.post(f"/api/contracts/{cid}/chat", json={"message": "What is the bond?"}, headers=H)
    assert r.status_code == 503 and "GROQ_API_KEY" in r.json()["detail"]


def test_chat_streams_and_saves_history(alice, monkeypatch):
    cid = alice.get("/api/contracts").json()[0]["id"]
    seen = {}

    def fake_stream(messages, **kw):
        seen["system"] = messages[0]["content"]
        yield "The bond is "
        yield "₹2,00,000 [#3]."

    monkeypatch.setattr(chat, "llm_available", lambda: True)
    monkeypatch.setattr(chat, "stream_chat", fake_stream)
    r = alice.post(f"/api/contracts/{cid}/chat", json={"message": "How much is the bond?"}, headers=H)
    assert r.status_code == 200 and r.text == "The bond is ₹2,00,000 [#3]."
    assert "Service Bond" in seen["system"] and "employee" in seen["system"]
    hist = alice.get(f"/api/contracts/{cid}/chat").json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant"]
    alice.delete(f"/api/contracts/{cid}/chat", headers=H)
    assert alice.get(f"/api/contracts/{cid}/chat").json()["messages"] == []


def test_retrieval_prefers_relevant_passage():
    text = ("Clause about rent and payment schedule.\n\n" * 30) + "The employee must serve a bond of two years.\n\n" + ("Unrelated filler text here.\n\n" * 500)
    ctx, whole = chat.select_context(text, "how long is the bond", [])
    assert not whole and "bond of two years" in ctx


def test_tts_falls_back_to_browser_voice(alice):
    r = alice.post("/api/tts", json={"text": "Hello **there** [#2]", "lang": "en"}, headers=H)
    assert r.status_code == 503 and r.json()["fallback_text"] == "Hello there flag 2"


def test_delete_document(alice):
    cid = alice.get("/api/contracts").json()[0]["id"]
    assert alice.delete(f"/api/contracts/{cid}", headers=H).json()["ok"]
    assert alice.get(f"/api/contracts/{cid}").status_code == 404
