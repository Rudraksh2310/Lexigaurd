"""
auth.py — accounts and sessions.

* Passwords: hashed with scrypt (Python standard library, memory-hard,
  random salt per user). Plain passwords are never stored or logged.
* Sessions: a random 256-bit token in an HTTP-only, SameSite=Lax cookie.
  The database keeps only the token's SHA-256 hash, so a database leak
  doesn't expose live sessions.
* CSRF: every state-changing /api request must carry the header
  `X-Requested-With: LexiGuard` (the frontend's api() helper adds it);
  other websites can't add custom headers to a cross-site request.
* Abuse control: simple in-memory rate limits on login, signup and
  other expensive endpoints.

Endpoints (router mounted at /api/auth):
    POST /signup   {name, email, password, role}
    POST /login    {email, password, remember}
    POST /logout
    GET  /me
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from db import AuthSession, User, get_db

COOKIE_NAME = "lg_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")  # true behind HTTPS
SESSION_DAYS_REMEMBER = 30
SESSION_DAYS_DEFAULT = 1
ROLES = ("employee", "business", "freelancer", "legal", "student", "other")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64),
                            n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


# Used to spend the same time on unknown emails as on wrong passwords
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def password_problem(password: str) -> str | None:
    if len(password) < 8:
        return "Use at least 8 characters."
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Use at least one letter and one number."
    if len(password) > 128:
        return "Use 128 characters or fewer."
    return None


# ---------------------------------------------------------------------------
# Rate limiting (in-memory; fine for a single server process)
# ---------------------------------------------------------------------------

class RateLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Records an attempt; returns False if the key is over the limit."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


login_limiter = RateLimiter(limit=8, window_seconds=15 * 60)
signup_limiter = RateLimiter(limit=20, window_seconds=60 * 60)  # per network; a lab may share one IP


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(db: Session, response: Response, user: User, remember: bool) -> None:
    token = secrets.token_urlsafe(32)
    days = SESSION_DAYS_REMEMBER if remember else SESSION_DAYS_DEFAULT
    db.add(AuthSession(token_hash=_token_hash(token), user_id=user.id,
                       expires_at=datetime.now(timezone.utc) + timedelta(days=days)))
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/",
        max_age=days * 86400 if remember else None,  # no max_age = ends when the browser closes
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    sess = db.get(AuthSession, _token_hash(token))
    if not sess:
        return None
    if _aware(sess.expires_at) < datetime.now(timezone.utc):
        db.delete(sess)
        db.commit()
        return None
    return db.get(User, sess.user_id)


def current_user(user: User | None = Depends(current_user_optional)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return user


def user_json(u: User) -> dict:
    return {"id": str(u.id), "name": u.name, "email": u.email, "role": u.role,
            "created_at": u.created_at.isoformat() if u.created_at else None}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    role: str | None = None


class LoginIn(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    remember: bool = False


@router.post("/signup")
def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if not signup_limiter.hit(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many sign-ups from this network. Try again in an hour.")
    email = body.email.strip().lower()
    name = re.sub(r"\s+", " ", body.name).strip()
    if not name:
        raise HTTPException(status_code=400, detail={"field": "name", "message": "Enter your name."})
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail={"field": "email", "message": "Enter a valid email address."})
    problem = password_problem(body.password)
    if problem:
        raise HTTPException(status_code=400, detail={"field": "password", "message": problem})
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail={"field": "email",
                                                     "message": "An account with this email already exists. Sign in instead."})
    user = User(name=name, email=email, password_hash=hash_password(body.password),
                role=body.role if body.role in ROLES else None)
    db.add(user)
    db.commit()
    start_session(db, response, user, remember=True)
    return user_json(user)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    key = f"{client_ip(request)}|{email}"
    if not login_limiter.hit(key):
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Wait 15 minutes and try again.")
    user = db.query(User).filter(User.email == email).first()
    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok:
        raise HTTPException(status_code=401, detail="That email and password don't match an account.")
    login_limiter.reset(key)
    start_session(db, response, user, remember=body.remember)
    return user_json(user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        sess = db.get(AuthSession, _token_hash(token))
        if sess:
            db.delete(sess)
            db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_json(user)


def owned_contract(db: Session, user: User, contract_id: str):
    """Returns the user's contract or raises 404 — also for other people's
    contracts, so their existence isn't revealed."""
    import uuid as _uuid
    from db import Contract
    try:
        cid = _uuid.UUID(str(contract_id))
    except ValueError:
        raise HTTPException(status_code=404, detail="Document not found.")
    c = db.get(Contract, cid)
    if not c or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    return c
