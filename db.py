"""
PostgreSQL storage for credentials (long-term). Used when DATABASE_URL is set.
Schema: credentials (email, password, refresh_token, client_id, client_secret, status fresh|used).
"""
import os
import re
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

# Same pattern as storage.py for parsing upload lines
CRED_LINE_PATTERN = re.compile(
    r"^([^|]+)\|([^|]*)\|([^|]+)\|([^|]+)(?:\|([^|]*))?$",
    re.IGNORECASE,
)

# When pasting in Telegram, newlines can become spaces. Split merged lines by " space before email|..."
_SPLIT_MERGED = re.compile(r" (?=[^|]+@[^|]+\|[^|]*\|)")


def _normalize_cred_lines(cred_lines: list[str]) -> list[str]:
    """Split by newline already done by caller; split any long line that looks like multiple credentials (space before email|...)."""
    out = []
    for line in cred_lines:
        line = (line or "").strip()
        if not line or line.startswith("#"):
            continue
        if line.count("|") >= 4 and "@" in line:
            parts = _SPLIT_MERGED.split(line)
            for p in parts:
                p = p.strip()
                if p:
                    out.append(p)
        else:
            out.append(line)
    return out


def _parse_line(line: str) -> dict | None:
    line = (line or "").strip()
    if not line or line.startswith("#"):
        return None
    m = CRED_LINE_PATTERN.match(line)
    if not m:
        return None
    mail, _pass, refresh_token, client_id, client_secret = m.groups()
    return {
        "mail": mail.strip(),
        "pass": (_pass or "").strip(),
        "refresh_token": refresh_token.strip(),
        "client_id": client_id.strip(),
        "client_secret": (client_secret or "").strip() or None,
    }


@contextmanager
def _conn():
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    conn = psycopg2.connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create credentials table if it does not exist."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS credentials (
                    id SERIAL PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL DEFAULT '',
                    refresh_token TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    client_secret TEXT,
                    status TEXT NOT NULL DEFAULT 'fresh' CHECK (status IN ('fresh', 'used')),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_credentials_status ON credentials(status);
                CREATE INDEX IF NOT EXISTS idx_credentials_email_lower ON credentials(LOWER(email));
            """)


def get_next() -> dict | None:
    """Take one fresh credential, mark it used, return it. Returns None if no fresh stock."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                UPDATE credentials
                SET status = 'used'
                WHERE id = (
                    SELECT id FROM credentials
                    WHERE status = 'fresh'
                    ORDER BY created_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING email AS mail, password AS pass, refresh_token, client_id, client_secret
            """)
            row = cur.fetchone()
    if not row:
        return None
    return dict(row)


def get_by_email(email: str) -> dict | None:
    """Find credential by email (case-insensitive). Returns dict with mail, pass, refresh_token, client_id, client_secret."""
    if not email or "@" not in email:
        return None
    email = email.strip().lower()
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT email AS mail, password AS pass, refresh_token, client_id, client_secret FROM credentials WHERE LOWER(email) = %s",
                (email,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def add_to_fresh(cred_lines: list[str]) -> tuple[int, list[str]]:
    """Insert valid lines as fresh credentials. Skips invalid/duplicate. Returns (added_count, errors)."""
    cred_lines = _normalize_cred_lines(cred_lines)
    added = 0
    errors = []
    creds = []
    seen = set()
    for line in cred_lines:
        line = (line or "").strip()
        if not line or line.startswith("#"):
            continue
        cred = _parse_line(line)
        if not cred:
            errors.append(f"Invalid line: {line[:50]}...")
            continue
        mail_lower = (cred.get("mail") or "").strip().lower()
        if mail_lower in seen:
            errors.append(f"Duplicate: {cred.get('mail')}")
            continue
        seen.add(mail_lower)
        creds.append(cred)

    if not creds:
        return added, errors

    with _conn() as conn:
        with conn.cursor() as cur:
            # Get existing emails to report duplicates
            cur.execute("SELECT LOWER(email) FROM credentials")
            existing = {r[0] for r in cur.fetchall()}
            for c in creds:
                mail_lower = (c.get("mail") or "").strip().lower()
                if mail_lower in existing:
                    errors.append(f"Duplicate: {c.get('mail')}")
                    continue
                try:
                    cur.execute(
                        """
                        INSERT INTO credentials (email, password, refresh_token, client_id, client_secret, status)
                        VALUES (%s, %s, %s, %s, %s, 'fresh')
                        """,
                        (
                            c["mail"].strip(),
                            c.get("pass") or "",
                            c["refresh_token"],
                            c["client_id"],
                            c.get("client_secret"),
                        ),
                    )
                    added += 1
                    existing.add(mail_lower)
                except Exception as e:
                    errors.append(f"{c.get('mail', '?')}: {e}")
    return added, errors


def stock_counts() -> tuple[int, int]:
    """Return (fresh_count, used_count)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, COUNT(*) FROM credentials GROUP BY status")
            rows = dict(cur.fetchall())
    return (rows.get("fresh") or 0, rows.get("used") or 0)
