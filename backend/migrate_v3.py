"""
migrate_v3.py — upgrades an EXISTING lexiguard database (from the first
versions of the project) to the accounts + chat version. Safe to run more
than once. New databases don't need it: tables are created on startup.

    python migrate_v3.py
    python migrate_v3.py --claim you@example.com   # give old, ownerless documents to this account

What it does
  * creates users, auth_sessions and chat_messages if missing
  * adds the newer columns to contracts and red_flags
  * optionally assigns documents analyzed before accounts existed to one
    user (otherwise nobody can see them)
"""

import sys

from sqlalchemy import text

from db import IS_SQLITE, Contract, SessionLocal, User, engine, init_db

PG_STATEMENTS = [
    "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS document_type VARCHAR(64)",
    "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS perspective VARCHAR(32)",
    "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS document_text TEXT",
    "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS engine VARCHAR(16)",
    "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS translations_json TEXT",
    "ALTER TABLE contracts ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE",
    "ALTER TABLE red_flags ADD COLUMN IF NOT EXISTS start_offset INTEGER",
    "ALTER TABLE red_flags ADD COLUMN IF NOT EXISTS end_offset INTEGER",
    "CREATE INDEX IF NOT EXISTS ix_contracts_user_id ON contracts (user_id)",
    "CREATE INDEX IF NOT EXISTS ix_red_flags_contract_id ON red_flags (contract_id)",
    "CREATE INDEX IF NOT EXISTS ix_contracts_uploaded_at ON contracts (uploaded_at)",
]

if __name__ == "__main__":
    init_db()  # creates the new tables first (users must exist before the foreign key)
    print("ok: new tables created if missing")
    if IS_SQLITE:
        print("SQLite database: columns come from create_all; delete the .db file to rebuild if needed.")
    else:
        with engine.begin() as conn:
            for stmt in PG_STATEMENTS:
                conn.execute(text(stmt))
                print("ok:", stmt)

    if len(sys.argv) == 3 and sys.argv[1] == "--claim":
        email = sys.argv[2].strip().lower()
        s = SessionLocal()
        try:
            user = s.query(User).filter(User.email == email).first()
            if not user:
                raise SystemExit(f"No account with email {email}. Sign up on the website first.")
            n = s.query(Contract).filter(Contract.user_id.is_(None)).update({Contract.user_id: user.id})
            s.commit()
            print(f"Assigned {n} older document(s) to {email}.")
        finally:
            s.close()
    print("Migration complete.")
