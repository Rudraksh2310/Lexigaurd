"""
check_setup.py — checks everything LexiGuard needs and says how to fix
what's broken. Run it from the backend folder with the virtual
environment active:

    python check_setup.py

It sends a few tiny test requests to Groq (a handful of tokens) and to the
voice services. Nothing from your documents is sent.
"""

import asyncio
import io
import os
import sys
import time
import wave

OK, BAD, WARN = "  [ OK ] ", "  [FAIL] ", "  [WARN] "
problems = 0


def say(mark, msg, fix=None):
    global problems
    if mark == BAD:
        problems += 1
    print(mark + msg)
    if fix:
        print("         -> " + fix)


print("\nLexiGuard setup check\n")

# 1. Python + packages ------------------------------------------------------
print("Packages")
v = sys.version_info
say(OK if v >= (3, 10) else BAD, f"Python {v.major}.{v.minor}", None if v >= (3, 10) else "Install Python 3.10 or newer.")
for mod, pip_name, needed in [("fastapi", "fastapi", True), ("uvicorn", "uvicorn[standard]", True),
                              ("sqlalchemy", "SQLAlchemy", True), ("multipart", "python-multipart", True),
                              ("requests", "requests", True), ("docx", "python-docx", True), ("pypdf", "pypdf", True),
                              ("reportlab", "reportlab", True), ("edge_tts", "edge-tts", False), ("gtts", "gTTS", False)]:
    try:
        __import__(mod)
        say(OK, pip_name)
    except ImportError:
        say(BAD if needed else WARN, f"{pip_name} is not installed", f"python -m pip install {pip_name}")

# 2. .env + database ----------------------------------------------------------
print("\nSettings and database")
from pathlib import Path
env = Path(__file__).resolve().parent / ".env"
say(OK if env.exists() else BAD, f".env found at {env}" if env.exists() else ".env is missing",
    None if env.exists() else "Copy .env.example to .env and fill it in.")
try:
    import db
    from sqlalchemy import text
    with db.engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    db.init_db()
    safe = db.DATABASE_URL.split("@")[-1] if "@" in db.DATABASE_URL else db.DATABASE_URL
    say(OK, f"Database connected ({safe}); tables are up to date")
except Exception as err:
    msg = str(err).splitlines()[0][:160]
    fix = "Check DATABASE_URL in .env."
    if "password authentication failed" in msg:
        fix = "The PostgreSQL password in DATABASE_URL is wrong (write @ as %40)."
    elif "does not exist" in msg:
        fix = "Run: python create_db.py"
    elif "could not connect" in msg or "Connection refused" in msg:
        fix = "PostgreSQL isn't running. Start it (Services > postgresql) or use sqlite:///./lexiguard.db"
    say(BAD, f"Database: {msg}", fix)

# 3. AI ------------------------------------------------------------------------
print("\nAI (Groq)")
import llm_client as L
prov = L._provider()
if not prov:
    say(BAD, "No AI key found", "Put GROQ_API_KEY=... in backend/.env")
else:
    key = prov["key"]
    say(OK, f"{prov['name']} key found ({key[:6]}…{key[-4:]})" if key else f"{prov['name']} configured")
    try:
        models = L.list_models(prov)
        say(OK, f"Key accepted; {len(models)} models available")
        if prov["model"] not in models:
            alt = next((m for m in L.PREFERRED_MODELS if m in models), None)
            say(WARN, f"LLM_MODEL '{prov['model']}' isn't offered to this key any more",
                f"LexiGuard will switch automatically, but set LLM_MODEL={alt} in .env" if alt else "Pick a model from console.groq.com")
        else:
            say(OK, f"Model {prov['model']} is available")
    except Exception as err:
        import requests
        if isinstance(err, requests.exceptions.SSLError):
            say(BAD, "Secure connection to Groq failed (SSL)", "This network is blocking or inspecting HTTPS. Try a phone hotspot.")
        elif isinstance(err, requests.exceptions.ConnectionError):
            say(BAD, "Can't reach api.groq.com", "Check the internet connection, firewall or proxy.")
        elif isinstance(err, requests.exceptions.HTTPError) and err.response is not None and err.response.status_code in (401, 403):
            say(BAD, "Groq rejected the key", "Create a new key at console.groq.com/keys and put it in .env")
        else:
            say(BAD, f"Listing models failed: {err}")
    try:
        t = time.time()
        out = L.chat_completion([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=5)
        say(OK, f"Chat answer received in {time.time() - t:.1f}s ('{out.strip()[:20]}')")
    except L.LLMError as err:
        say(BAD, f"Chat request failed: {err}")
    try:
        pieces = list(L.stream_chat([{"role": "user", "content": "Count from 1 to 5, separated by spaces."}], max_tokens=20))
        say(OK if len(pieces) > 1 else WARN, f"Streaming works ({len(pieces)} pieces)" if len(pieces) > 1 else
            "Streaming came back in one piece (a proxy may be buffering); answers still work, just not word by word")
    except L.LLMError as err:
        say(BAD, f"Streaming failed: {err}")
    try:
        hi = L.chat_completion([{"role": "system", "content": "Translate to Hindi. Reply with the translation only."},
                                {"role": "user", "content": "You must give three months' notice."}], max_tokens=60)
        dev = sum(1 for c in hi if "ऀ" <= c <= "ॿ")
        say(OK if dev > 3 else WARN, f"Hindi translation works: {hi.strip()[:60]}" if dev > 3 else f"Translation didn't look like Hindi: {hi[:60]}")
    except L.LLMError as err:
        say(BAD, f"Hindi translation failed: {err}")
    try:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000)
        L.transcribe(buf.getvalue(), "test.wav", "en")
        say(OK, "Voice typing (Whisper) works")
    except L.LLMError as err:
        say(BAD, f"Voice typing failed: {err}", "The draft page's microphone will fall back to the browser's own speech typing.")

# 4. Voice ---------------------------------------------------------------------
print("\nRead aloud")
engine = os.getenv("TTS_ENGINE", "edge").lower()
if engine == "browser":
    say(WARN, "TTS_ENGINE=browser: the browser's own voice is used (on Windows, install a Hindi voice in Settings > Time & language > Speech)")
else:
    try:
        import edge_tts

        async def _try(voice, txt):
            data = b""
            async for chunk in edge_tts.Communicate(txt, voice).stream():
                if chunk["type"] == "audio":
                    data += chunk["data"]
            return len(data)
        for lang, voice, txt in [("English", os.getenv("TTS_VOICE_EN", "en-IN-NeerjaNeural"), "Hello"),
                                 ("Hindi", os.getenv("TTS_VOICE_HI", "hi-IN-SwaraNeural"), "नमस्ते")]:
            try:
                n = asyncio.run(_try(voice, txt))
                say(OK if n else BAD, f"Microsoft voice, {lang} ({voice})" + ("" if n else ": no audio"))
            except Exception as err:
                say(WARN, f"Microsoft voice, {lang} failed: {err.__class__.__name__} {str(err)[:80]}",
                    "Try: python -m pip install -U edge-tts   (gTTS or the browser voice will be used meanwhile)")
    except ImportError:
        say(WARN, "edge-tts not installed", "python -m pip install edge-tts")
    try:
        from gtts import gTTS
        b = io.BytesIO()
        gTTS("नमस्ते", lang="hi").write_to_fp(b)
        say(OK, "Google voice backup (Hindi) works")
    except ImportError:
        say(WARN, "gTTS not installed (backup voice)", "python -m pip install gTTS")
    except Exception as err:
        say(WARN, f"Google voice backup failed: {err.__class__.__name__}")

print("\n" + ("Everything looks good. Start the app with:  python -m uvicorn api:app --reload --port 8000"
              if not problems else f"{problems} problem(s) found. Fix the [FAIL] lines above, then run this again."))
print()
