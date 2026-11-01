"""
drafts.py — write a legal document with the AI.

Flow on the draft page
  1. Pick a type (rent agreement, NDA, offer letter ...).
  2. Describe it by typing or speaking (speech -> text with Whisper), and
     optionally fill in key details.
  3. POST /api/drafts/questions: the AI lists the important details that
     are still missing (at most 6). The user answers or skips them.
  4. POST /api/drafts: the draft is written and streamed onto the page,
     then saved. Missing facts become [placeholders]; nothing is invented.
  5. POST /api/drafts/{id}/revise: "make the notice period 60 days" etc.
     Every AI rewrite keeps the previous version so it can be restored.
  6. Download as Word/PDF, or POST /api/drafts/{id}/check to run the
     draft through LexiGuard's red-flag check.

Drafts are written in light Markdown: "# " title, "## " clause headings,
"- " lists, **bold**, [placeholders]. After the document the model writes
a line "---NOTES---" and a few practical notes; those are shown beside the
draft but are not part of it.
"""

import io
import json
import logging
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import RateLimiter, current_user
from db import Draft, SessionLocal, User, get_db
from llm_client import LLMError, _try_parse_json, chat_completion, llm_available, stream_chat, transcribe

router = APIRouter(prefix="/api", tags=["drafting"])
log = logging.getLogger("lexiguard.drafts")
draft_limiter = RateLimiter(limit=40, window_seconds=60 * 60)
voice_limiter = RateLimiter(limit=60, window_seconds=10 * 60)
NOTES_MARK = "---NOTES---"
CHANGES_MARK = "---CHANGES---"
MAX_VERSIONS = 12

# ---------------------------------------------------------------------------
# Draft types and the details each one asks for (all optional)
# ---------------------------------------------------------------------------

def F(key, label, kind="text", placeholder="", options=None):
    d = {"key": key, "label": label, "type": kind, "placeholder": placeholder}
    if options:
        d["options"] = options
    return d


DRAFT_TYPES = {
    "rental": {
        "label": "Rent agreement", "hint": "Leave and licence or lease for a flat, room or shop",
        "sides": ["landlord", "tenant"], "check_type": "rental",
        "fields": [F("landlord", "Owner's full name"), F("tenant", "Tenant's full name"),
                   F("property", "Property address", "textarea"), F("rent", "Monthly rent", placeholder="₹25,000"),
                   F("deposit", "Security deposit", placeholder="₹50,000"), F("start", "Start date", "date"),
                   F("months", "Duration", placeholder="11 months"), F("lockin", "Lock-in period", placeholder="6 months"),
                   F("notice", "Notice to leave", placeholder="1 month"), F("city", "City")],
    },
    "nda": {
        "label": "Non-disclosure agreement", "hint": "Keep business information confidential",
        "sides": ["disclosing party", "receiving party"], "check_type": "nda",
        "fields": [F("party_a", "Disclosing party"), F("party_b", "Receiving party"),
                   F("mutual", "Who shares information?", "select", options=["One side shares", "Both sides share"]),
                   F("purpose", "Purpose", "textarea", "Discussing a possible partnership"),
                   F("years", "How long it lasts", placeholder="2 years"), F("city", "City for disputes")],
    },
    "freelance": {
        "label": "Freelance agreement", "hint": "Work for a client as an independent professional",
        "sides": ["freelancer", "client"], "check_type": "freelance",
        "fields": [F("freelancer", "Freelancer's name"), F("client", "Client's name"),
                   F("scope", "The work", "textarea", "Design and build a 5-page website"),
                   F("fee", "Fee", placeholder="₹60,000"), F("schedule", "Payment schedule", placeholder="50% upfront, 50% on delivery"),
                   F("deadline", "Deadline", "date"),
                   F("ip", "Who owns the work?", "select", options=["Client, after full payment", "Freelancer, client gets a licence"])],
    },
    "service": {
        "label": "Service agreement", "hint": "A business provides ongoing services to a client",
        "sides": ["service provider", "client"], "check_type": "service_agreement",
        "fields": [F("provider", "Service provider"), F("client", "Client"), F("services", "Services", "textarea"),
                   F("fees", "Fees", placeholder="₹1,20,000 per month"), F("term", "Term", placeholder="12 months"),
                   F("payment", "Payment terms", placeholder="Within 30 days of invoice")],
    },
    "offer_letter": {
        "label": "Offer letter", "hint": "Offer a job with the main terms of employment",
        "sides": ["company", "employee"], "check_type": "employment",
        "fields": [F("company", "Company name"), F("candidate", "Candidate's name"), F("role", "Job title"),
                   F("ctc", "Annual CTC", placeholder="₹8,00,000"), F("joining", "Joining date", "date"),
                   F("location", "Work location"), F("probation", "Probation", placeholder="6 months"),
                   F("notice", "Notice period", placeholder="30 days")],
    },
    "legal_notice": {
        "label": "Legal notice", "hint": "A formal demand, such as unpaid dues or a deposit not returned",
        "sides": [], "check_type": "other_legal",
        "fields": [F("sender", "From (your name)"), F("recipient", "To (name)"), F("recipient_address", "Their address", "textarea"),
                   F("issue", "What happened", "textarea", "Deposit of ₹40,000 not returned since March"),
                   F("demand", "What you want", placeholder="Return the deposit"),
                   F("days", "Days to respond", placeholder="15")],
    },
    "custom": {
        "label": "Something else", "hint": "Describe any agreement, letter or declaration you need",
        "sides": [], "check_type": "auto",
        "fields": [F("title", "What is it called?", placeholder="Partnership deed"), F("parties", "Who is involved?", "textarea")],
    },
}


def _type(key: str) -> dict:
    if key not in DRAFT_TYPES:
        raise HTTPException(status_code=400, detail="Choose a document type.")
    return DRAFT_TYPES[key]


@router.get("/draft-types")
def draft_types():
    return [{"value": k, **{x: v[x] for x in ("label", "hint", "sides", "fields")}} for k, v in DRAFT_TYPES.items()]


# ---------------------------------------------------------------------------
# Voice typing
# ---------------------------------------------------------------------------

@router.post("/transcribe")
def transcribe_audio(file: UploadFile = File(...), language: str = Form("auto"), user: User = Depends(current_user)):
    if not voice_limiter.hit(str(user.id)):
        raise HTTPException(status_code=429, detail="Too many recordings in a short time. Wait a few minutes.")
    audio = file.file.read(25 * 1024 * 1024 + 1)
    if not audio:
        raise HTTPException(status_code=400, detail="The recording was empty. Try again and speak a little longer.")
    if len(audio) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="That recording is too long. Keep each recording under about 10 minutes.")
    try:
        text = transcribe(audio, file.filename or "speech.webm", None if language == "auto" else language,
                          prompt="A person describing a legal agreement in India, possibly mixing Hindi and English.")
    except LLMError as err:
        raise HTTPException(status_code=503, detail=str(err))
    return {"text": text}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def _facts(t: dict, fields: dict, description: str, answers: list[dict]) -> str:
    lines = []
    for f in t["fields"]:
        v = str(fields.get(f["key"], "") or "").strip()
        if v:
            lines.append(f"- {f['label']}: {v}")
    for a in answers or []:
        q, ans = str(a.get("question", "")).strip(), str(a.get("answer", "")).strip()
        if q and ans:
            lines.append(f"- {q} {ans}")
    desc = (description or "").strip()
    return ("Details provided:\n" + ("\n".join(lines) if lines else "- (none filled in)") +
            "\n\nThe user's description, in their own words (may be spoken, informal, or mix Hindi and English):\n" +
            (desc if desc else "(none)"))


QUESTIONS_SYSTEM = """You help prepare a legal document draft. Look at what the user has given and list the important details that are still missing — only details without which the document would be incomplete or unclear (names of parties, amounts, dates, duration, key obligations).
Never ask for something the user already gave, even informally in the description. Ask at most 6 questions, most important first, each short and plain. If nothing important is missing, return an empty list.
Also suggest a short title for the document.
Return ONLY JSON: {"title": "...", "questions": [{"id": "q1", "question": "...", "hint": "an example answer"}]}"""


DRAFT_SYSTEM = """You are an experienced Indian legal drafter. Write a complete, well-structured {label} that is ready for the user to review.

Rules
- Use only the facts the user gave. Where a needed fact is missing, write a clear placeholder in square brackets, for example [Owner's full address] or [Date]. Never invent names, amounts, dates, addresses or ID numbers.
- Follow Indian law and practice unless the user asks otherwise, but don't cite specific section numbers unless you are certain of them.
- {favour}
- Plain, precise language. Avoid archaic legalese ("hereinafter", "witnesseth") where a plain word works. Define terms once and use them consistently.
- Typical structure: title; date and place; the parties; background (if useful); numbered clauses with short headings; signatures (with witnesses where customary). Include the clauses this kind of document normally needs, such as payment, duration, termination, notice, dispute resolution and governing law, as relevant.
- {language}
- Format: "# " for the title, "## " for each numbered clause heading (for example "## 3. Rent and deposit"), normal paragraphs, "- " for lists, **bold** sparingly. Signature lines as "Signature: ____________".
- Output the document only — no introduction or comment before it.
- After the document, write a line containing only {notes_mark} and then 3 to 6 short bullet points the user should know before signing (for example stamp duty and registration rules that vary by state, documents to attach, who should sign). These notes are not part of the document."""

REVISE_SYSTEM = """You are an experienced Indian legal drafter editing an existing draft. Apply the user's requested change carefully.
- Return the complete revised document, in the same format and language, changing only what the request needs (plus anything that must change to stay consistent, such as clause numbers or cross-references).
- Never invent facts; keep [placeholders] for missing facts.
- Output the document only, then a line containing only {changes_mark}, then one short sentence saying what you changed."""


def _favour_rule(t: dict, favour: str | None) -> str:
    if favour and favour in t["sides"]:
        return (f"Write it to protect the {favour}: choose terms that favour the {favour} while staying fair, "
                f"reasonable and enforceable — no clauses a court would strike down as one-sided.")
    return "Keep it balanced and fair to both sides."


def _lang_rule(language: str) -> str:
    if language == "hi":
        return ("Write the whole document in formal but clear Hindi (Devanagari). Where Indians usually use an "
                "English legal term, you may add it in brackets once, for example सुरक्षा राशि (Security Deposit).")
    return "Write in clear Indian English (₹ for money, DD Month YYYY for dates)."


def split_notes(text: str) -> tuple[str, str]:
    if NOTES_MARK in text:
        doc, notes = text.split(NOTES_MARK, 1)
        return doc.strip(), notes.strip()
    return text.strip(), ""


def split_changes(text: str) -> tuple[str, str]:
    if CHANGES_MARK in text:
        doc, ch = text.split(CHANGES_MARK, 1)
        return doc.strip(), ch.strip()
    return text.strip(), ""


def _clean_doc(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```[a-z]*\s*|\s*```$", "", t)  # stray code fences
    return t.strip()


# ---------------------------------------------------------------------------
# Routes: questions, create (stream), revise (stream)
# ---------------------------------------------------------------------------

class DraftInputs(BaseModel):
    doc_type: str
    fields: dict = Field(default_factory=dict)
    description: str = Field(default="", max_length=12000)
    answers: list[dict] = Field(default_factory=list)
    language: str = "en"
    favour: str | None = None
    title: str | None = Field(default=None, max_length=200)


def _need_ai():
    if not llm_available():
        raise HTTPException(status_code=503, detail="Drafting needs the AI key. Add GROQ_API_KEY to backend/.env and restart.")


@router.post("/drafts/questions")
def draft_questions(body: DraftInputs, user: User = Depends(current_user)):
    t = _type(body.doc_type)
    _need_ai()
    if not (body.description.strip() or any(str(v).strip() for v in body.fields.values())):
        raise HTTPException(status_code=400, detail="Describe the document, or fill in a few details, first.")
    prompt = f"Document type: {t['label']}\n\n" + _facts(t, body.fields, body.description, body.answers)
    try:
        out = _try_parse_json(chat_completion([{"role": "system", "content": QUESTIONS_SYSTEM},
                                               {"role": "user", "content": prompt}],
                                              temperature=0.2, json_mode=True, max_tokens=700))
    except LLMError as err:
        raise HTTPException(status_code=503, detail=str(err))
    out = out if isinstance(out, dict) else {}
    qs = []
    for i, q in enumerate(out.get("questions") or []):
        if isinstance(q, dict) and str(q.get("question", "")).strip():
            qs.append({"id": f"q{i + 1}", "question": str(q["question"]).strip()[:200], "hint": str(q.get("hint") or "")[:80]})
    title = str(out.get("title") or t["label"]).strip()[:120]
    return {"title": title, "questions": qs[:6]}


def _draft_json(d: Draft, full: bool = True) -> dict:
    t = DRAFT_TYPES.get(d.doc_type, {})
    out = {"id": str(d.id), "title": d.title, "doc_type": d.doc_type, "type_label": t.get("label", "Document"),
           "language": d.language, "favour": d.favour,
           "created_at": d.created_at.isoformat() if d.created_at else None,
           "updated_at": d.updated_at.isoformat() if d.updated_at else None,
           "preview": re.sub(r"[#*\[\]_]+", "", (d.content or ""))[:180].strip()}
    if full:
        versions = json.loads(d.versions_json) if d.versions_json else []
        out.update(content=d.content or "", notes=d.notes or "",
                   inputs=json.loads(d.inputs_json) if d.inputs_json else {},
                   versions=[{"index": i, "label": v.get("label"), "at": v.get("at")} for i, v in enumerate(versions)])
    return out


def _owned_draft(db: Session, user: User, draft_id: str) -> Draft:
    import uuid as _uuid
    try:
        did = _uuid.UUID(str(draft_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Draft not found.")
    d = db.get(Draft, did)
    if not d or d.user_id != user.id:
        raise HTTPException(status_code=404, detail="Draft not found.")
    return d


def _push_version(d: Draft, label: str):
    if not (d.content or "").strip():
        return
    versions = json.loads(d.versions_json) if d.versions_json else []
    versions.append({"content": d.content, "notes": d.notes, "label": label,
                     "at": datetime.now(timezone.utc).isoformat()})
    d.versions_json = json.dumps(versions[-MAX_VERSIONS:], ensure_ascii=False)


def _stream_and_save(draft_id, messages, max_tokens, mode: str):
    """Starts the AI stream (raising a clean HTTP error if it can't start),
    then streams text to the page and saves the result at the end."""
    gen = stream_chat(messages, temperature=0.25, max_tokens=max_tokens)
    try:
        first = next(gen, "")
    except LLMError as err:
        raise HTTPException(status_code=503, detail=str(err))

    def body():
        parts, failed = [first], False
        if first:
            yield first
        try:
            for piece in gen:
                parts.append(piece)
                yield piece
        except Exception as err:
            failed = True
            msg = str(err) if isinstance(err, LLMError) else "The AI stopped responding."
            yield f"\n\n{NOTES_MARK}\n- The draft was cut off: {msg} Use 'Ask for changes' to continue, or start again."
        text = "".join(parts)
        s = SessionLocal()
        try:
            d = s.get(Draft, draft_id)
            if not d:
                return
            if mode == "revise":
                doc, changes = split_changes(text)
                doc = _clean_doc(doc)
                if doc and not failed:
                    _push_version(d, "Before: " + (changes or "AI change")[:80])
                    d.content = doc
            else:
                doc, notes = split_notes(text)
                d.content = _clean_doc(doc)
                d.notes = notes
                m = re.match(r"\s*#\s+(.+)", d.content or "")
                if not d.title or d.title == "Untitled draft":
                    d.title = (m.group(1).replace("**", "").strip()[:200] if m else
                               DRAFT_TYPES.get(d.doc_type, {}).get("label", "Draft"))
            s.commit()
        finally:
            s.close()

    return StreamingResponse(body(), media_type="text/plain; charset=utf-8",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "X-Draft-Id": str(draft_id)})


@router.post("/drafts")
def create_draft(body: DraftInputs, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _type(body.doc_type)
    _need_ai()
    if not draft_limiter.hit(str(user.id)):
        raise HTTPException(status_code=429, detail="You've written a lot of drafts this hour. Try again later.")
    if not (body.description.strip() or any(str(v).strip() for v in body.fields.values())):
        raise HTTPException(status_code=400, detail="Describe the document, or fill in a few details, first.")
    language = "hi" if body.language == "hi" else "en"
    favour = body.favour if body.favour in t["sides"] else None
    d = Draft(user_id=user.id, title=(body.title or "").strip()[:200] or "Untitled draft", doc_type=body.doc_type,
              language=language, favour=favour, content="",
              inputs_json=json.dumps(body.model_dump(), ensure_ascii=False))
    db.add(d)
    db.commit()
    system = DRAFT_SYSTEM.format(label=t["label"].lower() if body.doc_type != "custom" else
                                 (body.fields.get("title") or "legal document"),
                                 favour=_favour_rule(t, favour), language=_lang_rule(language), notes_mark=NOTES_MARK)
    user_msg = f"Write the {t['label'].lower()}.\n\n" + _facts(t, body.fields, body.description, body.answers)
    return _stream_and_save(d.id, [{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
                            6000 if language == "hi" else 3800, "create")


class ReviseIn(BaseModel):
    instruction: str = Field(min_length=2, max_length=3000)


@router.post("/drafts/{draft_id}/revise")
def revise_draft(draft_id: str, body: ReviseIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = _owned_draft(db, user, draft_id)
    _need_ai()
    if not (d.content or "").strip():
        raise HTTPException(status_code=400, detail="This draft is empty. Write it first.")
    if not draft_limiter.hit(str(user.id)):
        raise HTTPException(status_code=429, detail="Too many changes in a short time. Try again later.")
    messages = [{"role": "system", "content": REVISE_SYSTEM.format(changes_mark=CHANGES_MARK)},
                {"role": "user", "content": f"CURRENT DRAFT:\n<<<\n{d.content}\n>>>\n\nREQUESTED CHANGE: {body.instruction.strip()}"}]
    return _stream_and_save(d.id, messages, 6000 if d.language == "hi" else 4000, "revise")


# ---------------------------------------------------------------------------
# Routes: list, read, edit, versions, delete
# ---------------------------------------------------------------------------

@router.get("/drafts")
def list_drafts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.query(Draft).filter(Draft.user_id == user.id).order_by(Draft.updated_at.desc()).all()
    return [_draft_json(d, full=False) for d in rows]


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _draft_json(_owned_draft(db, user, draft_id))


class DraftEdit(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, max_length=200000)
    save_version: bool = False


@router.put("/drafts/{draft_id}")
def edit_draft(draft_id: str, body: DraftEdit, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = _owned_draft(db, user, draft_id)
    if body.save_version:
        _push_version(d, "Saved by you")
    if body.title is not None and body.title.strip():
        d.title = body.title.strip()[:200]
    if body.content is not None:
        d.content = body.content
    d.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "updated_at": d.updated_at.isoformat()}


@router.post("/drafts/{draft_id}/restore/{index}")
def restore_version(draft_id: str, index: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = _owned_draft(db, user, draft_id)
    versions = json.loads(d.versions_json) if d.versions_json else []
    if not 0 <= index < len(versions):
        raise HTTPException(status_code=404, detail="That version doesn't exist.")
    chosen = versions.pop(index)
    d.versions_json = json.dumps(versions, ensure_ascii=False)
    _push_version(d, "Before restoring an earlier version")
    d.content = chosen["content"]
    db.commit()
    return _draft_json(d)


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = _owned_draft(db, user, draft_id)
    db.delete(d)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Export and check
# ---------------------------------------------------------------------------

def parse_blocks(md: str) -> list[tuple[str, str]]:
    """Light Markdown -> [(kind, text)] with kind in title|h2|h3|li|p|blank."""
    out = []
    for line in (md or "").replace("\r", "").split("\n"):
        s = line.rstrip()
        if not s.strip():
            out.append(("blank", ""))
        elif s.startswith("# "):
            out.append(("title", s[2:].strip()))
        elif s.startswith("## "):
            out.append(("h2", s[3:].strip()))
        elif s.startswith("### "):
            out.append(("h3", s[4:].strip()))
        elif re.match(r"^\s*[-*•]\s+", s):
            out.append(("li", re.sub(r"^\s*[-*•]\s+", "", s)))
        else:
            out.append(("p", s.strip()))
    return out


def plain_text(md: str) -> str:
    lines = []
    for kind, text in parse_blocks(md):
        text = text.replace("**", "")
        lines.append({"title": text.upper(), "li": "- " + text, "blank": ""}.get(kind, text))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _docx(d: Draft) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
    from docx.shared import Pt

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Nirmala UI" if d.language == "hi" else "Cambria"
    st.font.size = Pt(11.5)

    def runs(par, text):
        for bold_part in re.split(r"(\*\*[^*]+\*\*)", text):
            if not bold_part:
                continue
            bold = bold_part.startswith("**") and bold_part.endswith("**")
            body = bold_part[2:-2] if bold else bold_part
            for piece in re.split(r"(\[[^\]]{1,80}\])", body):
                if not piece:
                    continue
                r = par.add_run(piece)
                r.bold = bold
                if piece.startswith("[") and piece.endswith("]"):
                    r.font.highlight_color = WD_COLOR_INDEX.YELLOW

    for kind, text in parse_blocks(d.content):
        if kind == "blank":
            continue
        if kind == "title":
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run(text.replace("**", "").upper() if d.language != "hi" else text.replace("**", ""))
            r.bold = True
            r.font.size = Pt(15)
        elif kind in ("h2", "h3"):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(10)
            r = p.add_run(text.replace("**", ""))
            r.bold = True
        elif kind == "li":
            runs(doc.add_paragraph(style="List Bullet"), text)
        else:
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(6)
            runs(p, text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _pdf(d: Draft) -> bytes:
    from xml.sax.saxutils import escape
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    from report import _pdf_font

    font, unicode_ok = _pdf_font()
    base = ParagraphStyle("b", fontName=font, fontSize=11, leading=16.5, alignment=TA_JUSTIFY, spaceAfter=6)
    title = ParagraphStyle("t", parent=base, fontSize=15, leading=20, alignment=TA_CENTER, spaceAfter=14)
    h2 = ParagraphStyle("h", parent=base, fontSize=11.5, spaceBefore=10, spaceAfter=4, alignment=0)
    li = ParagraphStyle("l", parent=base, leftIndent=14, bulletIndent=4)

    def fmt(text):
        t = escape(text if unicode_ok else text.replace("₹", "Rs. "))
        t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
        return re.sub(r"(\[[^\]]{1,80}\])", r'<font backColor="#FEF08A">\1</font>', t)

    story = []
    for kind, text in parse_blocks(d.content):
        if kind == "blank":
            continue
        if kind == "title":
            story.append(Paragraph(f"<b>{fmt(text.replace('**', '')).upper()}</b>", title))
        elif kind in ("h2", "h3"):
            story.append(Paragraph(f"<b>{fmt(text.replace('**', ''))}</b>", h2))
        elif kind == "li":
            story.append(Paragraph(fmt(text), li, bulletText="•"))
        else:
            story.append(Paragraph(fmt(text), base))
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm,
                      bottomMargin=20 * mm, title=d.title, author="LexiGuard AI draft").build(story or [Spacer(1, 1)])
    return buf.getvalue()


@router.get("/drafts/{draft_id}/export")
def export_draft(draft_id: str, format: str = Query("docx", pattern="^(pdf|docx)$"),
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = _owned_draft(db, user, draft_id)
    if not (d.content or "").strip():
        raise HTTPException(status_code=400, detail="This draft is empty.")
    if format == "pdf" and d.language == "hi":
        raise HTTPException(status_code=409, detail="For Hindi drafts, use Print and choose 'Save as PDF' so the Hindi text renders correctly.")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", d.title)[:60].strip("_") or "draft"
    if format == "docx":
        return Response(_docx(d), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{stem}.docx"'})
    return Response(_pdf(d), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'})


@router.post("/drafts/{draft_id}/check")
def check_draft(draft_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Runs the draft through the normal red-flag check and returns the new document id."""
    from parser import analyze_and_store
    d = _owned_draft(db, user, draft_id)
    if not (d.content or "").strip():
        raise HTTPException(status_code=400, detail="This draft is empty.")
    t = DRAFT_TYPES.get(d.doc_type, DRAFT_TYPES["custom"])
    side = d.favour
    if not side and t["sides"]:
        side = t["sides"][-1]   # tenant, receiving party, client, employee: the usual weaker side
    stem = re.sub(r"[^A-Za-z0-9 ._-]+", "", d.title)[:80].strip() or "Draft"
    try:
        out = analyze_and_store(f"{stem} (draft).txt", plain_text(d.content).encode("utf-8"), doc_type=t["check_type"],
                                perspective=side, force=True, user_id=user.id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err))
    return {"contract_id": out["contract_id"]}
