"""
File-based storage: fresh_stock.txt (unused Hotmail credentials), used.txt (used).
One credential per line: mail|pass|refresh_token|client_id or ...|client_secret
"""
import os
import re
import fcntl
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
FRESH_FILE = DATA_DIR / "fresh_stock.txt"
USED_FILE = DATA_DIR / "used.txt"

# One credential per line: mail|pass|refresh_token|client_id or ...|client_secret
CRED_LINE_PATTERN = re.compile(
    r"^([^|]+)\|([^|]*)\|([^|]+)\|([^|]+)(?:\|([^|]*))?$",
    re.IGNORECASE,
)


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


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
        "_raw": line,
    }


def _cred_to_line(cred: dict) -> str:
    parts = [cred["mail"], cred.get("pass", ""), cred["refresh_token"], cred["client_id"]]
    if cred.get("client_secret"):
        parts.append(cred["client_secret"])
    return "|".join(parts)


def load_fresh() -> list[dict]:
    """Load all credentials from fresh_stock.txt."""
    _ensure_data_dir()
    if not FRESH_FILE.exists():
        return []
    with open(FRESH_FILE, "r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            lines = f.read().strip().splitlines()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    out = []
    for line in lines:
        c = _parse_line(line)
        if c:
            out.append(c)
    return out


def load_used() -> list[dict]:
    """Load all lines from used.txt (for lookup by email)."""
    _ensure_data_dir()
    if not USED_FILE.exists():
        return []
    with open(USED_FILE, "r", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_SH)
        try:
            lines = f.read().strip().splitlines()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    out = []
    for line in lines:
        c = _parse_line(line)
        if c:
            out.append(c)
    return out


def get_next() -> dict | None:
    """Take one credential from fresh (first line), remove it, append to used. Returns cred or None."""
    _ensure_data_dir()
    if not FRESH_FILE.exists():
        return None
    with open(FRESH_FILE, "r+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            content = f.read()
            lines = [L for L in content.strip().splitlines() if L.strip() and not L.strip().startswith("#")]
            if not lines:
                return None
            first = lines[0]
            rest = "\n".join(lines[1:]) + ("\n" if lines[1:] else "")
            f.seek(0)
            f.write(rest)
            f.truncate()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    cred = _parse_line(first)
    if not cred:
        return None
    append_to_used(cred)
    return cred


def append_to_used(cred: dict) -> None:
    """Append one credential line to used.txt."""
    _ensure_data_dir()
    with open(USED_FILE, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(_cred_to_line(cred) + "\n")
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def get_by_email(email: str) -> dict | None:
    """Find credential by mail (case-insensitive) in fresh first, then used."""
    if not email or "@" not in email:
        return None
    email = email.strip().lower()
    for cred in load_fresh():
        if (cred.get("mail") or "").strip().lower() == email:
            return cred
    for cred in load_used():
        if (cred.get("mail") or "").strip().lower() == email:
            return cred
    return None


# When pasting in Telegram, newlines can become spaces. Split merged lines by " space before email|..."
_SPLIT_MERGED = re.compile(r" (?=[^|]+@[^|]+\|[^|]*\|)")


def _normalize_cred_lines(cred_lines: list[str]) -> list[str]:
    """Split by newline, then split any long line that looks like multiple credentials (space before email|...)."""
    out = []
    for line in cred_lines:
        line = (line or "").strip()
        if not line or line.startswith("#"):
            continue
        # If line has many pipes and contains @, it might be "cred1 cred2 cred3" merged
        if line.count("|") >= 4 and "@" in line:
            parts = _SPLIT_MERGED.split(line)
            for p in parts:
                p = p.strip()
                if p:
                    out.append(p)
        else:
            out.append(line)
    return out


def add_to_fresh(cred_lines: list[str]) -> tuple[int, list[str]]:
    """
    Add credential lines to fresh_stock. Skips invalid/duplicate.
    Handles pasted text where newlines were turned into spaces (splits on space before email|...).
    Returns (added_count, list of parse errors or duplicate emails).
    """
    cred_lines = _normalize_cred_lines(cred_lines)
    _ensure_data_dir()
    existing_emails = {c.get("mail", "").strip().lower() for c in load_fresh()}
    existing_emails.update(c.get("mail", "").strip().lower() for c in load_used())
    added = 0
    errors = []
    to_append = []
    for line in cred_lines:
        line = (line or "").strip()
        if not line or line.startswith("#"):
            continue
        cred = _parse_line(line)
        if not cred:
            errors.append(f"Invalid line: {line[:50]}...")
            continue
        mail_lower = (cred.get("mail") or "").strip().lower()
        if mail_lower in existing_emails:
            errors.append(f"Duplicate: {cred.get('mail')}")
            continue
        existing_emails.add(mail_lower)
        to_append.append(_cred_to_line(cred))
        added += 1
    if to_append:
        with open(FRESH_FILE, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                for ln in to_append:
                    f.write(ln + "\n")
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return added, errors


def stock_counts() -> tuple[int, int]:
    """Return (fresh_count, used_count)."""
    return len(load_fresh()), len(load_used())
