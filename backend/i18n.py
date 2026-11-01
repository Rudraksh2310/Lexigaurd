"""
i18n.py — shows a document's explanations in Hindi.

POST /api/contracts/{id}/translate  {"lang": "hi"}
  -> {"lang": "hi", "summary": "...", "flags": {flag_id: {category, plain_english_explanation,
      why_it_matters, suggested_action}}, "complete": true}

The summary and each flag's explanation are translated with the AI in
small batches (Hindi uses many tokens, and free-tier limits are per
minute). Results are saved on the contract, so each document is
translated once; later requests return instantly. The clause quotes stay
in the document's own language.
"""

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import RateLimiter, current_user, owned_contract
from db import RedFlag, User, get_db
from llm_client import LLMError, _try_parse_json, chat_completion, llm_available

router = APIRouter(prefix="/api/contracts", tags=["language"])
log = logging.getLogger("lexiguard.i18n")
translate_limiter = RateLimiter(limit=30, window_seconds=10 * 60)
BATCH = 4
FIELDS = ("category", "plain_english_explanation", "why_it_matters", "suggested_action")

SYSTEM = """You translate explanations from a legal-document checker into simple, natural Hindi for ordinary Indian readers.
Rules:
- Write in Devanagari script. Use everyday Hindi, not heavy Sanskritised words.
- Keep common English legal or HR terms that Indians normally say in English (for example notice period, bond, probation, deposit, lock-in) — write them in Devanagari or keep them in English in brackets.
- Keep every number, amount, date and percentage exactly as it is.
- Keep the same meaning; don't add or remove advice.
- Return ONLY JSON in exactly the shape you were given, with the same ids and keys, values translated."""


class TranslateIn(BaseModel):
    lang: str = "hi"


def is_mostly_devanagari(text: str) -> bool:
    letters = re.findall(r"[A-Za-zऀ-ॿ]", text or "")
    return bool(letters) and sum(1 for c in letters if "ऀ" <= c <= "ॿ") / len(letters) > 0.4


def _translate_batch(payload: dict) -> dict:
    raw = chat_completion([{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": "Translate the values of this JSON into Hindi:\n" +
                            json.dumps(payload, ensure_ascii=False)}],
                          temperature=0.2, json_mode=True, max_tokens=3500)
    out = _try_parse_json(raw)
    if not isinstance(out, dict):
        raise LLMError("The Hindi translation came back in the wrong format. Try again.")
    return out


@router.post("/{contract_id}/translate")
def translate(contract_id: str, body: TranslateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.lang != "hi":
        raise HTTPException(status_code=400, detail="Only Hindi is available right now.")
    c = owned_contract(db, user, contract_id)
    cache = {}
    if c.translations_json:
        try:
            cache = json.loads(c.translations_json)
        except ValueError:
            cache = {}
    hi = cache.setdefault("hi", {"summary": None, "flags": {}})
    flags = db.query(RedFlag).filter(RedFlag.contract_id == c.id).all()
    todo = [f for f in flags if str(f.id) not in hi["flags"]]

    if (todo or (c.contract_summary and not hi.get("summary"))):
        if not llm_available():
            raise HTTPException(status_code=503, detail="Hindi needs the AI key. Add GROQ_API_KEY to backend/.env and restart.")
        if not translate_limiter.hit(str(user.id)):
            raise HTTPException(status_code=429, detail="Too many translation requests. Wait a few minutes.")

    error = None
    try:
        if c.contract_summary and not hi.get("summary"):
            out = _translate_batch({"summary": c.contract_summary})
            if isinstance(out.get("summary"), str) and out["summary"].strip():
                hi["summary"] = out["summary"].strip()
        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            payload = {"items": [{"id": str(f.id), **{k: getattr(f, k) or "" for k in FIELDS}} for f in batch]}
            out = _translate_batch(payload)
            items = out.get("items") if isinstance(out.get("items"), list) else []
            for item in items:
                fid = str(item.get("id", ""))
                if fid in {str(f.id) for f in batch}:
                    hi["flags"][fid] = {k: str(item.get(k) or "").strip() for k in FIELDS}
            c.translations_json = json.dumps(cache, ensure_ascii=False)
            db.commit()  # keep progress even if a later batch fails
    except LLMError as err:
        error = str(err)
    c.translations_json = json.dumps(cache, ensure_ascii=False)
    db.commit()

    complete = all(str(f.id) in hi["flags"] for f in flags) and (hi.get("summary") or not c.contract_summary)
    if not complete and error and not hi["flags"] and not hi.get("summary"):
        raise HTTPException(status_code=503, detail=error)
    return {"lang": "hi", "summary": hi.get("summary"), "flags": hi["flags"], "complete": bool(complete), "error": error}
