"""
create_db.py — creates the target Postgres database (e.g. "lexiguard")
if it doesn't already exist, using the same credentials as DATABASE_URL
in your .env file.

Run this once, from VS Code's integrated terminal:
    python create_db.py

Why this connects to "postgres" instead of "lexiguard":
Postgres won't let you create a database while connected to that same
database, and CREATE DATABASE can't run inside a transaction block —
so this connects to the default "postgres" maintenance database with
autocommit enabled, and issues the command from there.
"""

import os

import psycopg2
from dotenv import load_dotenv
from sqlalchemy.engine import make_url

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise SystemExit(
        "DATABASE_URL not found. Check that .env exists in this folder "
        "and contains a DATABASE_URL= line."
    )

url = make_url(DATABASE_URL)
target_db = url.database

if not target_db:
    raise SystemExit("Couldn't parse a database name out of DATABASE_URL.")

conn = psycopg2.connect(
    dbname="postgres",  # always connect to the maintenance db to create others
    user=url.username,
    password=url.password,
    host=url.host or "localhost",
    port=url.port or 5432,
)
conn.autocommit = True  # required: CREATE DATABASE cannot run inside a transaction

try:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target_db,))
        if cur.fetchone():
            print(f"Database '{target_db}' already exists — nothing to do.")
        else:
            # Database names can't be parameterized in psycopg2 (they're
            # identifiers, not values), so we validate + quote manually.
            if not target_db.replace("_", "").isalnum():
                raise SystemExit(f"Refusing to create suspicious database name: {target_db!r}")
            cur.execute(f'CREATE DATABASE "{target_db}"')
            print(f"Created database '{target_db}'.")
finally:
    conn.close()

print("Next: run `python db.py` to create the tables inside it.")
