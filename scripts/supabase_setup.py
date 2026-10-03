"""Set up the linked Supabase project for the app and write .env.

What it does, through the Supabase CLI (which must be signed in and linked to the project):

1. Applies migrations/001_init.sql (schema "hazard", tables, row-level security). Idempotent.
2. Creates the database role "hazard_app" (or resets its password) with a freshly generated
   password. Only a SCRAM verifier is sent to the database; the password itself is written
   to .env and nowhere else.
3. Applies migrations/002_app_role.sql (rights and policies for hazard_app).
4. Writes .env with the connection string, a session secret and the first administrator.

Usage:
    python scripts/supabase_setup.py --admin-email you@example.com
    python scripts/supabase_setup.py --admin-email you@example.com --rotate   # new DB password, keep the rest

It never prints a secret.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
ROLE = "hazard_app"


def scram_verifier(password: str, iterations: int = 4096) -> str:
    """The value Postgres stores for a SCRAM-SHA-256 password, computed locally."""
    salt = os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def run_sql(sql: str | None = None, file: Path | None = None) -> None:
    """Run SQL on the linked project through the Management API. Output is not echoed."""
    tmp = None
    try:
        if sql is not None:
            fd, name = tempfile.mkstemp(suffix=".sql")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(sql)
            tmp = file = Path(name)
        proc = subprocess.run(["supabase", "db", "query", "--linked", "-f", str(file)],
                              cwd=ROOT, capture_output=True, text=True, shell=(os.name == "nt"))
        if proc.returncode != 0:
            # the CLI's error text does not include the SQL it was given
            raise SystemExit(f"The Supabase CLI failed:\n{proc.stderr.strip()[-800:]}")
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def read_env() -> dict[str, str]:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
            if m:
                out[m.group(1)] = m.group(2)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--admin-email", required=True, help="Email of the first administrator of the app")
    ap.add_argument("--rotate", action="store_true", help="Replace the database password in an existing .env")
    args = ap.parse_args()

    pooler = ROOT / "supabase" / ".temp" / "pooler-url"
    ref_file = ROOT / "supabase" / ".temp" / "project-ref"
    if not pooler.exists() or not ref_file.exists():
        raise SystemExit("This folder is not linked to a Supabase project. Run: supabase link --project-ref <ref>")
    ref = ref_file.read_text().strip()
    m = re.match(r"^postgresql://[^@]+@([^/]+)/(\w+)$", pooler.read_text().strip())
    if not m:
        raise SystemExit("Could not read the pooler address from supabase/.temp/pooler-url")
    host, database = m.group(1), m.group(2)

    existing = read_env()
    if existing and not args.rotate:
        raise SystemExit(".env already exists. Use --rotate to replace only the database password, "
                         "or delete .env to start again.")

    print("1/4 schema and tables")
    run_sql(file=ROOT / "migrations" / "001_init.sql")

    print(f"2/4 database role {ROLE}")
    db_password = secrets.token_urlsafe(30)          # URL-safe: needs no escaping in the connection string
    verifier = scram_verifier(db_password)
    run_sql(f"""
        do $$ begin
          if not exists (select 1 from pg_roles where rolname = '{ROLE}') then
            create role {ROLE} login noinherit nocreatedb nocreaterole;
          end if;
        end $$;
        alter role {ROLE} with login password '{verifier}';
    """)

    print("3/4 rights and policies")
    run_sql(file=ROOT / "migrations" / "002_app_role.sql")

    print("4/4 .env")
    url = f"postgresql://{ROLE}.{ref}:{db_password}@{host}/{database}"
    values = {
        "DATABASE_URL": url,
        "AUTO_CREATE_SCHEMA": "false",
        "STORAGE_BACKEND": "db",
        "SESSION_SECRET": existing.get("SESSION_SECRET") or secrets.token_urlsafe(48),
        "SESSION_HOURS": existing.get("SESSION_HOURS", "12"),
        "COOKIE_SECURE": existing.get("COOKIE_SECURE", "false"),
        "ADMIN_EMAIL": existing.get("ADMIN_EMAIL") or args.admin_email,
        "ADMIN_PASSWORD": existing.get("ADMIN_PASSWORD") or secrets.token_urlsafe(15),
        "ADMIN_NAME": existing.get("ADMIN_NAME", "Administrator"),
        "MAX_UPLOAD_MB": existing.get("MAX_UPLOAD_MB", "200"),
    }
    notes = {
        "DATABASE_URL": f"# Supabase project {ref}, session pooler, role {ROLE} (rights on schema hazard only)",
        "AUTO_CREATE_SCHEMA": "# The schema is managed by migrations/*.sql through the Supabase CLI",
        "STORAGE_BACKEND": "# Parsed zips are kept in the hazard.blobs table",
        "SESSION_SECRET": "# Signs the session cookie. Changing it signs everyone out",
        "COOKIE_SECURE": "# false while testing on http://127.0.0.1; set true when served over HTTPS",
        "ADMIN_EMAIL": "# First administrator, created once when the users table is empty.\n"
                       "# Sign in with these, then change the password in the app",
    }
    lines = ["# Written by scripts/supabase_setup.py. Holds secrets: do not share or commit this file.", ""]
    for k, v in values.items():
        if k in notes:
            lines.append(notes[k])
        lines.append(f"{k}={v}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Done. Wrote {ENV.name}. The administrator's first password is in that file as ADMIN_PASSWORD.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
