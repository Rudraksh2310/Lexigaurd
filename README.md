<<<<<<< HEAD
# LexiGuard AI — Legal Document Red Flag Extractor

Upload a contract, offer letter, rent agreement, NDA or terms of service.
LexiGuard checks that it really is a legal document, marks the risky
clauses (red = High, orange = Medium, yellow = Low) from the user's side,
explains each one in plain English or Hindi, answers questions about the
document in a chat (typed or spoken), reads explanations aloud, and
produces a downloadable PDF/Word report.

It can also **draft** documents: describe what you need by typing or
speaking (English, Hindi or a mix), answer a few follow-up questions, and
get a first draft with blanks for anything missing. Edit it, ask the AI for
changes, download it, or run it through the red-flag check.

Each user has an account; documents and drafts are private to it.

## Run it (Windows Command Prompt in VS Code)

```
cd backend
python -m venv .venv                 (first time only)
.venv\Scripts\activate.bat
python -m pip install -r requirements.txt
python create_db.py                  (first time only: creates the PostgreSQL database)
python check_setup.py                (checks the database, Groq key, Hindi, voice; says how to fix problems)
python -m uvicorn api:app --reload --port 8000
```

New columns and tables are added to an existing database automatically when
the server starts.

Open **http://localhost:8000**. The same server serves the website and the
API, so there's no separate frontend server any more.

Old documents analyzed before accounts existed belong to nobody. To move
them into your account: sign up on the website, then
`python migrate_v3.py --claim you@example.com`.

## Configuration — `backend/.env`

| Setting | What it does |
|---|---|
| `DATABASE_URL` | PostgreSQL connection. Encode special characters in the password (`@` → `%40`). For a quick test without PostgreSQL: `sqlite:///./lexiguard.db` |
| `GROQ_API_KEY` | Groq key for the analysis, chat and Hindi translation |
| `USE_LLM` | `true` = Groq analyzes documents (falls back to the built-in rules if Groq fails); `false` = rules only |
| `LLM_MODEL` / `LLM_FALLBACK_MODEL` | Main model, and the smaller one used when Groq's rate limit is hit |
| `TTS_ENGINE` | `edge` = Microsoft neural voices, with Google's voice (gTTS) as backup (online); `browser` = the browser's own voice, nothing sent anywhere |
| `WHISPER_MODEL` | Speech-to-text model for voice typing (default `whisper-large-v3-turbo` on Groq) |
| `TTS_VOICE_EN` / `TTS_VOICE_HI` | Voice names (default Indian English and Hindi voices) |
| `MAX_UPLOAD_MB` | Upload limit |
| `COOKIE_SECURE` | `true` once the site is served over HTTPS |

`.env` holds secrets and is listed in `.gitignore`, so it never goes to
GitHub. `.env.example` shows the format without real values.

## How it works

```
upload ─► extract text (PDF / DOCX incl. tables / TXT)
       ─► document check: is it legal? which type?       doc_gate.py
       ─► candidate clauses by keyword rules per type     rules.py, doc_types.py
       ─► Groq judges risk + explains (long files in parts) llm_client.py
       ─► grounding: every quote must exist in the text,  highlighter.py
          positions recorded for highlighting, score computed in code
       ─► saved under the user's account                  db.py
review page ─► highlights + hover explanations + flag list (English / Hindi)  i18n.py
           ─► chat: BM25 retrieval of relevant passages + all flags ─► Groq (streamed)   chat.py
           ─► voice: Edge-TTS (Hindi via Groq translation), cached; browser voice fallback  tts.py
           ─► report: annotated PDF / DOCX                report.py
draft page ─► speech to text (Whisper on Groq; browser speech as backup)    drafts.py
           ─► AI asks for missing details ─► streamed draft with [blanks]
           ─► edit / AI changes with versions ─► Word / PDF ─► red-flag check
```

If something AI-related doesn't work, run `python check_setup.py`. The
server also logs the exact reason (for example a rejected key, a retired
model, or a network blocking api.groq.com). If the configured model has been
retired, LexiGuard switches to an available one automatically and logs which.

## Files

```
backend/
  api.py          FastAPI app: documents API + serves the website
  auth.py         sign-up / sign-in, scrypt password hashing, cookie sessions, rate limits
  chat.py         document chat (retrieval, prompt, streaming, history)
  drafts.py       AI drafting: types, voice typing, questions, writing, changes, export
  i18n.py         Hindi explanations (translated once, then cached)
  tts.py          read aloud (English / Hindi): edge-tts, gTTS, browser
  check_setup.py  checks everything and says how to fix problems
  parser.py       the analysis pipeline
  doc_gate.py     legal-document check
  doc_types.py    document types and whose side each is judged from
  rules.py        keyword rule packs (general, employment, rental, terms of service)
  highlighter.py  quote matching, offsets, scoring
  llm_client.py   Groq / OpenAI / Ollama calls with retries and model fallback
  report.py       PDF / DOCX report
  db.py           tables: users, auth_sessions, contracts, red_flags, chat_messages, drafts
  migrate_v3.py   upgrade an older database
  tests/          python -m pytest tests -q   (uses a temporary SQLite DB, no AI calls)
frontend/
  index.html      landing page
  login.html, signup.html
  dashboard.html  "Your documents"
  analyze.html    "Check a document"
  review.html     the highlighted document, red flags, chat, voice, downloads
  draft.html      write a document with the AI
  assets/app.css, assets/app.js   shared styles and helpers
samples/          test documents (offer letter, service agreement, resume, borderline letter)
```

## Security notes

- Passwords: scrypt with a random salt per user. Sessions: random token in an
  HTTP-only SameSite cookie; only its hash is stored.
- Every document query is filtered by the signed-in user; someone else's
  document returns 404.
- State-changing requests need the `X-Requested-With: LexiGuard` header
  (blocks cross-site form attacks).
- Sign-in, sign-up, analysis, chat and voice are rate-limited.
- Document text is never written to logs. The text is sent to Groq for
  analysis and chat, and to Microsoft when `TTS_ENGINE=edge`.

LexiGuard is decision support, not legal advice.
=======
# Lexigaurd
An ai which helps consumers prevent from falling into legal traps by analysing and identifying clauses which might be used against them or can be used to exploit them. This website also helps user to draft any legal document in a very easy and systematic manner.
>>>>>>> 85b50387ec27fae9c2924e5511aaba57f5f8de91
