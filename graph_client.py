"""
Microsoft Graph client: refresh token -> access token, list inbox, detect OTP.
"""
import re
import requests

GRAPH_SCOPES = ["https://graph.microsoft.com/Mail.Read", "https://graph.microsoft.com/User.Read"]
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def get_access_token(client_id: str, refresh_token: str, client_secret: str | None = None) -> dict:
    """Exchange refresh_token for access_token. Returns token dict or raises."""
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
        "scope": " ".join(GRAPH_SCOPES),
    }
    if client_secret:
        data["client_secret"] = client_secret
    r = requests.post(TOKEN_URL, data=data)
    r.raise_for_status()
    return r.json()


def list_inbox_messages(access_token: str, top: int = 20) -> list[dict]:
    """Fetch recent inbox messages (subject, bodyPreview, receivedDateTime, from)."""
    url = f"{GRAPH_BASE}/me/mailFolders/inbox/messages"
    params = {"$top": top, "$orderby": "receivedDateTime desc", "$select": "subject,body,bodyPreview,receivedDateTime,from,isRead"}
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r.json().get("value", [])


# Only treat as OTP email if subject/body clearly indicate verification (not marketing).
VERIFICATION_PHRASES = [
    "verification code",
    "your verification code",
    "this code is valid",
    "enter this code",
    "verify your email",
    "security code",
    "one-time",
    "your code",
    "valid for 10 minutes",
    "valid for 5 minutes",
    "didn't request this code",
    "sign up",
]
# Subject must look like a verification email (e.g. "Zoom verification code", "511599 is your Zoom verification code").
VERIFICATION_SUBJECT_KEYWORDS = ["verification code", "verification code is", "verify your email", "security code", "your code"]


def _is_verification_email(subject: str, body_plain: str) -> bool:
    """True only if this looks like a real OTP/verification email, not marketing."""
    subject_lower = subject.lower()
    body_lower = body_plain.lower()
    # Subject: e.g. "Zoom verification code" or "511599 is your Zoom verification code"
    if any(kw in subject_lower for kw in VERIFICATION_SUBJECT_KEYWORDS):
        return True
    # Body: explicit verification wording
    if any(phrase in body_lower for phrase in VERIFICATION_PHRASES):
        return True
    return False


# Prefer the single code that appears right after "verification code", "this code", "enter this code", or standalone 5–6 digits.
PRIMARY_OTP_PATTERNS = [
    re.compile(r"verification code[.\s]*\n?\s*(\d{5,6})\b", re.I),
    re.compile(r"this code[.\s]*\n?\s*[:\s]*(\d{5,6})\b", re.I),
    re.compile(r"enter this code[.\s]*\n?\s*[:\s]*(\d{5,6})\b", re.I),
    re.compile(r"(?:code|otp)[\s:]+(\d{5,6})\b", re.I),
    re.compile(r"(\d{5,6})[\s.]*(?:is your|is the) (?:verification )?code", re.I),
    re.compile(r"^\s*(\d{5,6})\s*$", re.M),  # standalone line, 5–6 digits
]


def _extract_primary_otp(text: str) -> list[str]:
    """From a verification email body, extract the real OTP(s); prefer 5–6 digit codes after verification wording."""
    if not text:
        return []
    seen = set()
    for pat in PRIMARY_OTP_PATTERNS:
        for m in pat.finditer(text):
            code = m.group(1).strip()
            if len(code) in (5, 6) and code not in seen:
                seen.add(code)
    # If no strict match, fall back to single 5–6 digit code that appears in first half of body (often the only one)
    if not seen:
        for m in re.finditer(r"\b(\d{5,6})\b", text):
            code = m.group(1)
            if code not in seen:
                seen.add(code)
                break  # take at most one fallback
    return sorted(seen)


def get_otp_from_inbox(access_token: str, max_messages: int = 15) -> list[dict]:
    """
    Get recent inbox messages that are verification/OTP emails only; extract the primary code from each.
    Each item: { "subject", "from", "received", "body_preview", "otp_codes" }.
    """
    messages = list_inbox_messages(access_token, top=max_messages)
    results = []
    for msg in messages:
        subject = (msg.get("subject") or "").strip()
        body = msg.get("body", {})
        content = (body.get("content") or "") if isinstance(body, dict) else ""
        preview = (msg.get("bodyPreview") or "").strip()
        text = f"{subject}\n{preview}\n{content}"
        text_plain = re.sub(r"<[^>]+>", " ", text)
        if not _is_verification_email(subject, text_plain):
            continue
        codes = _extract_primary_otp(text_plain)
        if not codes:
            continue
        from_info = msg.get("from", {}).get("emailAddress", {})
        from_addr = from_info.get("address", "")
        from_name = from_info.get("name", from_addr)
        results.append({
            "subject": subject,
            "from": from_name or from_addr,
            "from_address": from_addr,
            "received": msg.get("receivedDateTime", ""),
            "body_preview": (preview or content[:200]).strip(),
            "otp_codes": codes,
        })
    return results
