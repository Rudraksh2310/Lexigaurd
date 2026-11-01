"""
api.py — LexiGuard AI server. Serves the website AND the API from one
address, so sign-in cookies just work.

Run from backend/:
    uvicorn api:app --reload --port 8000
then open http://localhost:8000

API (everything except /api/health, /api/config, /api/document-types and
/api/auth/* needs sign-in; users only ever see their own documents):

    POST   /api/auth/signup | /login | /logout     GET /api/auth/me      (auth.py)
    GET    /api/config                   what's switched on (AI, voice)
    GET    /api/document-types           options for the document-type picker
    POST   /api/analyze                  multipart: file, doc_type, perspective, force
    GET    /api/contracts                the signed-in user's documents
    GET    /api/contracts/{id}           one document with flags + text
    DELETE /api/contracts/{id}           delete it (flags, chat, everything)
    GET    /api/contracts/{id}/report    ?format=pdf|docx
    GET|POST|DELETE /api/contracts/{id}/chat                               (chat.py)
    POST   /api/tts                      read text aloud                   (tts.py)
    GET    /api/stats                    numbers for the documents page
    POST   /api/contracts/{id}/translate show explanations in Hindi            (i18n.py)
    GET    /api/ai/status                is the AI reachable?
    /api/draft-types, /api/transcribe, /api/drafts...  AI drafting          (drafts.py)
"""

import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func
from sqlalchemy.orm import Session

import auth
import chat
import drafts
import i18n
import tts
from auth import RateLimiter, current_user, owned_contract
from db import Contract, RedFlag, User, get_db, init_db
from doc_types import AUTO, DOC_TYPES, public_list
from llm_client import ai_status, llm_available
from parser import DocumentRejected, analyze_and_store
from report import build_docx, build_pdf

log = logging.getLogger("lexiguard")
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "10"))
ALLOWED_EXTENSIONS = (".pdf", ".docx", ".txt")
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
analyze_limiter = RateLimiter(limit=30, window_seconds=60 * 60)


@asynccontextmanager
async def lifespan(_app):
    init_db()
    if (FRONTEND_DIR / "index.html").exists():
        print(f"[LexiGuard] Website folder: {FRONTEND_DIR}")
        print("[LexiGuard] Open http://localhost:8000 in your browser.")
    else:
        print(f"[LexiGuard] WEBSITE NOT FOUND: expected index.html in {FRONTEND_DIR}")
        print("[LexiGuard] Put the 'frontend' folder next to the 'backend' folder, then restart.")
    yield


app = FastAPI(title="LexiGuard AI", lifespan=lifespan)

# Only needed if you serve the frontend from a different address
_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["Content-Disposition"])


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """State-changing API calls must come from our own pages (see auth.py)."""
    if (request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS")
            and request.headers.get("x-requested-with") != "LexiGuard"):
        return JSONResponse(status_code=403, content={"detail": "Request blocked (missing X-Requested-With header)."})
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(tts.router)
app.include_router(i18n.router)
app.include_router(drafts.router)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flag_json(f: RedFlag) -> dict:
    return {
        "id": str(f.id),
        "category": f.category,
        "risk_level": f.risk_level,
        "clause_text": f.clause_text,
        "section_reference": f.section_reference,
        "plain_english_explanation": f.plain_english_explanation,
        "why_it_matters": f.why_it_matters,
        "suggested_action": f.suggested_action,
        "start": f.start_offset,
        "end": f.end_offset,
    }


def _contract_detail(db: Session, c: Contract) -> dict:
    flags = (db.query(RedFlag).filter(RedFlag.contract_id == c.id)
             .order_by(RedFlag.start_offset.asc().nulls_last(), RedFlag.created_at).all())
    return {
        "id": str(c.id),
        "filename": c.filename,
        "uploaded_at": c.uploaded_at.isoformat() if c.uploaded_at else None,
        "contract_summary": c.contract_summary,
        "overall_risk_score": c.overall_risk_score,
        "total_flags": c.total_flags,
        "severity_counts": {"High": c.high_count, "Medium": c.medium_count, "Low": c.low_count},
        "document_type": c.document_type,
        "document_label": DOC_TYPES.get(c.document_type or "", {}).get("label", "Legal document"),
        "perspective": c.perspective,
        "engine": c.engine,
        "document_text": c.document_text,
        "flags": [_flag_json(f) for f in flags],
    }


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config():
    return {"ai": llm_available(), "voice_engine": tts.ENGINE, "max_upload_mb": MAX_UPLOAD_MB}


@app.get("/api/ai/status")
def get_ai_status(refresh: bool = False, user: User = Depends(current_user)):
    """Is the AI reachable right now? (cached for a minute)"""
    return ai_status(force=refresh)


@app.get("/api/document-types")
def document_types():
    return public_list()


# ---------------------------------------------------------------------------
# Documents (signed-in user's own only)
# ---------------------------------------------------------------------------

@app.post("/api/analyze")
def analyze_contract(
    file: UploadFile = File(...),
    doc_type: str = Form(AUTO),
    perspective: str | None = Form(None),
    force: bool = Form(False),
    user: User = Depends(current_user),
):
    # Plain `def`: FastAPI runs it in a worker thread, so the slow pipeline
    # (parsing, AI calls, database) doesn't block other requests.
    if not analyze_limiter.hit(str(user.id)):
        raise HTTPException(status_code=429, detail="You've analyzed a lot of documents this hour. Try again later.")
    if not file.filename or not file.filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Upload a PDF, DOCX, or TXT file.")

    content = file.file.read(int(MAX_UPLOAD_MB * 1024 * 1024) + 1)
    if not content:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"That file is larger than {MAX_UPLOAD_MB:g} MB.")

    try:
        return analyze_and_store(filename=file.filename[:500], content=content, doc_type=doc_type,
                                 perspective=perspective, force=force, user_id=user.id)
    except DocumentRejected as rej:
        c = rej.check
        raise HTTPException(status_code=422, detail={
            "code": "not_legal_document", "message": str(rej), "verdict": c["verdict"],
            "detected_label": c["detected_label"], "confidence": c["confidence"],
            "reasons": c["reasons"], "can_override": c["can_override"],
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        log.exception("Analysis failed (%d bytes)", len(content))  # never log document text
        raise HTTPException(status_code=500, detail="Analysis failed. Check the server log for details.")


@app.get("/api/contracts")
def list_contracts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = (db.query(Contract).filter(Contract.user_id == user.id)
            .order_by(Contract.uploaded_at.desc()).all())
    return [
        {
            "id": str(c.id),
            "filename": c.filename,
            "uploaded_at": c.uploaded_at.isoformat() if c.uploaded_at else None,
            "document_type": c.document_type,
            "document_label": DOC_TYPES.get(c.document_type or "", {}).get("label", "Legal document"),
            "perspective": c.perspective,
            "overall_risk_score": c.overall_risk_score,
            "contract_summary": c.contract_summary,
            "total_flags": c.total_flags,
            "high_count": c.high_count,
            "medium_count": c.medium_count,
            "low_count": c.low_count,
        }
        for c in rows
    ]


@app.get("/api/contracts/{contract_id}")
def get_contract(contract_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _contract_detail(db, owned_contract(db, user, contract_id))


@app.delete("/api/contracts/{contract_id}")
def delete_contract(contract_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = owned_contract(db, user, contract_id)
    db.delete(c)  # flags and chat go with it (cascade)
    db.commit()
    return {"ok": True}


@app.get("/api/contracts/{contract_id}/report")
def download_report(contract_id: str, format: str = Query("pdf", pattern="^(pdf|docx)$"),
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = _contract_detail(db, owned_contract(db, user, contract_id))
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.splitext(data["filename"] or "document")[0])[:60]
    if format == "docx":
        body = build_docx(data)
        media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        body = build_pdf(data)
        media = "application/pdf"
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f'attachment; filename="{stem}_LexiGuard_report.{format}"'})


@app.get("/api/stats")
def get_stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    mine = db.query(Contract).filter(Contract.user_id == user.id)
    total_contracts = mine.count()
    sums = db.query(func.coalesce(func.sum(Contract.total_flags), 0), func.coalesce(func.sum(Contract.high_count), 0),
                    func.coalesce(func.sum(Contract.medium_count), 0), func.coalesce(func.sum(Contract.low_count), 0)
                    ).filter(Contract.user_id == user.id).one()
    total_flags, high, medium, low = (int(x or 0) for x in sums)
    high_risk_contracts = mine.filter(Contract.overall_risk_score == "High").count()
    category_rows = (
        db.query(RedFlag.category, RedFlag.risk_level, func.count(RedFlag.id))
        .join(Contract, Contract.id == RedFlag.contract_id).filter(Contract.user_id == user.id)
        .group_by(RedFlag.category, RedFlag.risk_level).all()
    )
    return {
        "total_contracts": total_contracts,
        "total_flags": total_flags,
        "high_risk_contracts": high_risk_contracts,
        "avg_flags_per_contract": round(total_flags / total_contracts, 1) if total_contracts else 0,
        "severity_totals": {"High": high, "Medium": medium, "Low": low},
        "category_breakdown": [{"category": cat, "risk_level": lv, "count": n} for cat, lv, n in category_rows],
    }


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        detail = getattr(exc, "detail", None) or "Not found."
        return JSONResponse(status_code=404, content={"detail": detail})
    page = FRONTEND_DIR / "404.html"
    if page.exists():
        return Response(page.read_bytes(), status_code=404, media_type="text/html")
    return JSONResponse(status_code=404, content={"detail": "Not found."})


# The website itself (index.html, login.html, ...). Mounted last so /api wins.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="site")
else:
    @app.get("/", include_in_schema=False)
    def _no_site():
        return JSONResponse(status_code=404, content={
            "detail": f"LexiGuard's website folder wasn't found at {FRONTEND_DIR}. "
                      "Put the 'frontend' folder next to 'backend' and restart the server."})
