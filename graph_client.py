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


# Common OTP patterns: 4-8 digits, often with word boundaries or in "code: 123456" style
OTP_PATTERNS = [
    re.compile(r"\b(\d{4})\b"),
    re.compile(r"\b(\d{5})\b"),
    re.compile(r"\b(\d{6})\b"),
    re.compile(r"\b(\d{8})\b"),
    re.compile(r"(?:code|otp|verification|pin|password)[\s:]*(\d{4,8})", re.I),
    re.compile(r"(\d{4,8})[\s.]*(?:is your|is the)"),
]


def extract_otp_from_text(text: str) -> list[str]:
    """Extract likely OTP codes from message body/preview. Returns list of candidate codes."""
    if not text:
        return []
    codes = set()
    for pat in OTP_PATTERNS:
        for m in pat.finditer(text):
            codes.add(m.group(1).strip())
    return sorted(codes, key=len, reverse=True)  # prefer longer matches


def get_otp_from_inbox(access_token: str, max_messages: int = 15) -> list[dict]:
    """
    Get recent inbox messages and return those that contain a likely OTP.
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
        # Strip HTML tags for plain text search
        text_plain = re.sub(r"<[^>]+>", " ", text)
        codes = extract_otp_from_text(text_plain)
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
