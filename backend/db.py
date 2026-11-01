"""
db.py — database connection, ORM models, and table auto-creation for
LexiGuard AI.

Works with PostgreSQL (the normal setup) and SQLite (handy for a quick
local run or tests: DATABASE_URL=sqlite:///./lexiguard.db).

Tables
    users           one row per account
    auth_sessions   login sessions (only a hash of the cookie token is stored)
    contracts       one row per analyzed document, owned by a user
    red_flags       one row per flagged clause
    chat_messages   the document chat history

New databases: tables are created automatically on startup.
Existing databases from earlier versions: run `python migrate_v3.py` once.
"""

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Always read backend/.env, whatever folder the server is started from
load_dotenv(Path(__file__).resolve().parent / ".env")

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Integer, String, Text, Uuid,
                        create_engine, event)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


def _now():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = Column(String(120), nullable=False)
    email = Column(String(254), nullable=False, unique=True, index=True)  # stored lower-case
    password_hash = Column(String(255), nullable=False)
    role = Column(String(40), nullable=True)  # "employee", "business", "legal", "student", ...
    created_at = Column(DateTime(timezone=True), default=_now)
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    contracts = relationship("Contract", back_populates="user", cascade="all, delete-orphan")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token_hash = Column(String(64), primary_key=True)  # sha256 of the cookie value
    user_id = Column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=_now)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class Contract(Base):
    """One row per uploaded/analyzed document."""
    __tablename__ = "contracts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    filename = Column(String(512), nullable=False)
    uploaded_at = Column(DateTime(timezone=True), default=_now, index=True)

    detected_language = Column(String(32), nullable=True)
    was_translated = Column(Integer, default=0)

    contract_summary = Column(Text, nullable=True)
    overall_risk_score = Column(String(16), nullable=True)  # "Low" | "Medium" | "High"
    total_flags = Column(Integer, default=0)
    raw_text_length = Column(Integer, default=0)

    high_count = Column(Integer, default=0)
    medium_count = Column(Integer, default=0)
    low_count = Column(Integer, default=0)

    document_type = Column(String(64), nullable=True)   # see doc_types.py
    perspective = Column(String(32), nullable=True)     # e.g. "employee"
    document_text = Column(Text, nullable=True)         # extracted text the highlights point into
    engine = Column(String(16), nullable=True)          # "llm" or "rules"
    translations_json = Column(Text, nullable=True)     # cached Hindi explanations, see i18n.py

    user = relationship("User", back_populates="contracts")
    red_flags = relationship("RedFlag", back_populates="contract", cascade="all, delete-orphan")
    chat_messages = relationship("ChatMessage", back_populates="contract", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Contract {self.filename} ({self.overall_risk_score})>"


class RedFlag(Base):
    """One row per detected risky clause within a contract."""
    __tablename__ = "red_flags"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    contract_id = Column(Uuid, ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)

    category = Column(String(64), nullable=False)
    risk_level = Column(String(16), nullable=False)
    clause_text = Column(Text, nullable=False)
    section_reference = Column(String(128), nullable=True)
    plain_english_explanation = Column(Text, nullable=True)
    why_it_matters = Column(Text, nullable=True)
    suggested_action = Column(Text, nullable=True)

    start_offset = Column(Integer, nullable=True)  # position of clause_text in contracts.document_text
    end_offset = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=_now)

    contract = relationship("Contract", back_populates="red_flags")

    def __repr__(self):
        return f"<RedFlag {self.category} [{self.risk_level}]>"


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    contract_id = Column(Uuid, ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(16), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    failed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=_now)

    contract = relationship("Contract", back_populates="chat_messages")


class Draft(Base):
    """A document written with the AI on the draft page."""
    __tablename__ = "drafts"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id = Column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False, default="Untitled draft")
    doc_type = Column(String(40), nullable=False)
    language = Column(String(8), nullable=False, default="en")   # "en" | "hi"
    favour = Column(String(40), nullable=True)                   # side to protect, or None = balanced
    inputs_json = Column(Text, nullable=True)                    # details, description, answers
    content = Column(Text, nullable=True)                        # the draft (light Markdown)
    notes = Column(Text, nullable=True)                          # "before you sign" notes, not part of the document
    versions_json = Column(Text, nullable=True)                  # earlier versions [{content, label, at}]
    created_at = Column(DateTime(timezone=True), default=_now)
    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now, index=True)


# ---------------------------------------------------------------------------
# Engine / session setup
# ---------------------------------------------------------------------------

def _build_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD", "postgres")
    host = os.getenv("PGHOST", "localhost")
    port = os.getenv("PGPORT", "5432")
    dbname = os.getenv("PGDATABASE", "lexiguard")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"


DATABASE_URL = _build_database_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
)

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _sqlite_fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def _add_missing_columns() -> None:
    """Adds columns that newer versions introduced to tables created by an
    older version (create_all never alters existing tables). Only adds
    nullable columns; never drops or changes anything."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or col.primary_key:
                    continue
                ddl_type = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}'))
                print(f"[LexiGuard] database upgraded: added {table.name}.{col.name}")


def init_db() -> None:
    """Creates missing tables and columns. Safe to call on every startup."""
    Base.metadata.create_all(bind=engine)
    try:
        _add_missing_columns()
    except Exception as err:  # never block startup; migrate_v3.py remains available
        print(f"[LexiGuard] couldn't upgrade the database automatically ({err}). Run: python migrate_v3.py")


def get_db():
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


get_session = get_db  # old name


if __name__ == "__main__":
    init_db()
    safe = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    print(f"Tables created (or already existed) at: {safe}")
