"""
tts.py — read explanations and chat answers aloud, in English or Hindi.

POST /api/tts  {text, lang: "en" | "hi"}  -> audio/mpeg

* English text is spoken as written. For Hindi, text that is still in
  English is translated with the AI first (cached); text that is already
  Hindi (the page's Hindi view) is spoken directly.
* Voices, tried in order:
    1. edge-tts   — Microsoft neural voices (en-IN Neerja, hi-IN Swara)
    2. gTTS       — Google Translate's voice, a good backup for Hindi
    3. the browser's own voice (the page does this when the server answers
       503 with `fallback_text`)
  TTS_ENGINE=browser skips 1 and 2 so no text leaves the machine.
* Audio is cached on disk by a hash of (engine, voice, text).
"""

import hashlib
import json
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from auth import RateLimiter, current_user
from db import User

router = APIRouter(prefix="/api", tags=["voice"])
log = logging.getLogger("lexiguard.tts")
tts_limiter = RateLimiter(limit=120, window_seconds=10 * 60)

CACHE_DIR = Path(os.getenv("TTS_CACHE_DIR", Path(__file__).resolve().parent / "audio_cache"))
ENGINE = os.getenv("TTS_ENGINE", "edge").lower()          # "edge" or "browser"
VOICES = {
    "en": os.getenv("TTS_VOICE_EN", "en-IN-NeerjaNeural"),
    "hi": os.getenv("TTS_VOICE_HI", "hi-IN-SwaraNeural"),
}
MAX_CHARS = 3000


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    lang: str = "en"


def _key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:40]


def clean_for_speech(text: str) -> str:
    """Strips chat markdown so it isn't read out ('asterisk asterisk')."""
    t = re.sub(r"\[#(\d+)\]", r"flag \1", text)
    t = re.sub(r"(?m)^\s*suggested wording:?", "Suggested wording:", t, flags=re.I)
    t = re.sub(r"[*_`#>|]+", " ", t)
    t = re.sub(r"(?m)^\s*[-•]\s+", "", t)
    return re.sub(r"\s+", " ", t).strip()[:MAX_CHARS]


def is_hindi(text: str) -> bool:
    letters = re.findall(r"[A-Za-zऀ-ॿ]", text)
    return bool(letters) and sum(1 for c in letters if "ऀ" <= c <= "ॿ") / len(letters) > 0.4


def translate_to_hindi(text: str) -> str:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"hi_{_key('hi', text)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["text"]
    from llm_client import chat_completion
    out = chat_completion([
        {"role": "system", "content": "Translate the user's text into simple, natural spoken Hindi (Devanagari). "
                                      "Keep common English legal terms like 'notice period' or 'bond' where Indians "
                                      "usually say them in English. Keep numbers and amounts exact. "
                                      "Return only the translation, nothing else."},
        {"role": "user", "content": text},
    ], temperature=0.2, max_tokens=2000).strip()
    if not out:
        raise RuntimeError("empty translation")
    path.write_text(json.dumps({"text": out}, ensure_ascii=False), encoding="utf-8")
    return out


async def synth_edge(text: str, voice: str) -> Path:
    import edge_tts  # pip install edge-tts
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"edge_{_key(voice, text)}.mp3"
    if path.exists() and path.stat().st_size > 0:
        return path
    tmp = path.with_suffix(".part")
    await edge_tts.Communicate(text, voice).save(str(tmp))
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError("edge-tts returned no audio")
    tmp.replace(path)
    return path


def synth_gtts(text: str, lang: str) -> Path:
    from gtts import gTTS  # pip install gTTS
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"gtts_{_key(lang, text)}.mp3"
    if path.exists() and path.stat().st_size > 0:
        return path
    tmp = path.with_suffix(".part")
    gTTS(text=text, lang=lang, tld="co.in").save(str(tmp))
    tmp.replace(path)
    return path


def _fallback(text: str, lang: str, why: str) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": why, "fallback_text": text, "lang": lang})


@router.post("/tts")
async def tts(body: TTSIn, user: User = Depends(current_user)):
    lang = "hi" if body.lang == "hi" else "en"
    if not tts_limiter.hit(str(user.id)):
        raise HTTPException(status_code=429, detail="Too many voice requests. Wait a few minutes.")

    text = clean_for_speech(body.text)
    if not text:
        raise HTTPException(status_code=400, detail="There's nothing to read.")
    if lang == "hi" and not is_hindi(text):
        try:
            text = await run_in_threadpool(translate_to_hindi, text)
        except Exception as err:
            log.warning("Hindi translation for voice failed: %s", err)
            msg = str(err) if err.__class__.__name__ in ("LLMError", "LLMUnavailable") else "The Hindi translation didn't work."
            return _fallback(text, "en", f"{msg} Reading it in English instead.")

    if ENGINE == "browser":
        return _fallback(text, lang, "Server voice is off; using the browser voice.")

    problems = []
    try:
        path = await synth_edge(text, VOICES[lang])
        return FileResponse(path, media_type="audio/mpeg", headers={"X-TTS-Engine": "edge"})
    except ImportError:
        problems.append("edge-tts isn't installed")
    except Exception as err:
        problems.append(f"edge-tts: {err.__class__.__name__}")
        log.warning("edge-tts failed: %r", err)
    try:
        path = await run_in_threadpool(synth_gtts, text, lang)
        return FileResponse(path, media_type="audio/mpeg", headers={"X-TTS-Engine": "gtts"})
    except ImportError:
        problems.append("gTTS isn't installed")
    except Exception as err:
        problems.append(f"gTTS: {err.__class__.__name__}")
        log.warning("gTTS failed: %r", err)
    log.warning("Online voices unavailable (%s); the browser will speak instead.", "; ".join(problems))
    return _fallback(text, lang, "The online voices aren't reachable, so your browser's voice is used.")
