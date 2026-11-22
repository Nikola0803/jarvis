"""Gmail integration -- multi-account read/reply/send.

Auth flow (same OOB pattern as google_calendar.py):
  1. /mail auth [account]      -> returns URL to open
  2. /mail auth_code <code>    -> saves token, confirms

Scopes: readonly + modify + send so we can mark read and reply.
Uses the same credentials_file as Calendar (google_credentials.json).
"""
from __future__ import annotations

import asyncio
import base64
import email as email_lib
import email.mime.text
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]

_service_cache: dict[str, Any] = {}


# ── Service builder ────────────────────────────────────────────────────────

def _build_service(token_file: str, credentials_file: str):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if Path(token_file).exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_file, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(
                f"Token not found or expired at {token_file}. "
                "Run /mail auth to authenticate."
            )

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _get_service(account_name: str, token_file: str, credentials_file: str):
    key = f"{account_name}:{token_file}"
    if key not in _service_cache:
        _service_cache[key] = _build_service(token_file, credentials_file)
    return _service_cache[key]


def clear_cache():
    _service_cache.clear()


# ── OAuth2 ─────────────────────────────────────────────────────────────────

def start_auth_flow(credentials_file: str) -> tuple[str, Any]:
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
    # Google retired the "oob" copy-paste flow in 2022. Use a loopback redirect
    # instead: after approving, the browser lands on an unreachable
    # http://localhost/?code=... page -- the user copies the code out of the
    # address bar. Works headless and needs a "Desktop app" OAuth client.
    flow.redirect_uri = "http://localhost"
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account consent",
    )
    return auth_url, flow


def finish_auth_flow(flow, code: str, token_file: str) -> str:
    flow.fetch_token(code=code.strip())
    creds = flow.credentials
    Path(token_file).parent.mkdir(parents=True, exist_ok=True)
    with open(token_file, "w") as f:
        f.write(creds.to_json())
    clear_cache()
    try:
        import jwt as pyjwt
        payload = pyjwt.decode(creds.id_token, options={"verify_signature": False})
        return payload.get("email", "account")
    except Exception:
        return "account"


# ── Core operations ────────────────────────────────────────────────────────

def _decode_body(payload: dict) -> str:
    """Extract plain text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")

    if mime_type == "text/plain" and body_data:
        return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")

    if mime_type == "text/html" and body_data:
        raw = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
        return re.sub(r"<[^>]+>", "", raw).strip()

    for part in payload.get("parts", []):
        text = _decode_body(part)
        if text:
            return text

    return ""


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _parse_message(msg: dict) -> dict:
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    snippet = msg.get("snippet", "")
    body = _decode_body(payload)

    # Timestamp
    ts_ms = int(msg.get("internalDate", 0))
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else None

    return {
        "id": msg.get("id", ""),
        "thread_id": msg.get("threadId", ""),
        "subject": _header(headers, "subject"),
        "from": _header(headers, "from"),
        "to": _header(headers, "to"),
        "date": ts,
        "snippet": snippet,
        "body": body[:2000],  # cap for LLM context
        "labels": msg.get("labelIds", []),
        "unread": "UNREAD" in msg.get("labelIds", []),
    }


async def list_messages(
    token_file: str,
    credentials_file: str,
    account_name: str = "primary",
    unread_only: bool = True,
    max_results: int = 10,
    label_ids: list[str] | None = None,
) -> list[dict]:
    """List messages (unread inbox by default)."""
    def _fetch():
        svc = _get_service(account_name, token_file, credentials_file)
        query = "is:unread" if unread_only else ""
        labels = label_ids or ["INBOX"]
        result = svc.users().messages().list(
            userId="me",
            q=query,
            labelIds=labels,
            maxResults=max_results,
        ).execute()
        messages = result.get("messages", [])
        full = []
        for m in messages:
            msg = svc.users().messages().get(
                userId="me", id=m["id"], format="full"
            ).execute()
            full.append(_parse_message(msg))
        return full

    return await asyncio.get_event_loop().run_in_executor(None, _fetch)


async def get_message(
    token_file: str,
    credentials_file: str,
    message_id: str,
    account_name: str = "primary",
) -> dict | None:
    """Get a single message by ID."""
    def _fetch():
        svc = _get_service(account_name, token_file, credentials_file)
        msg = svc.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
        return _parse_message(msg)

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _fetch)
    except Exception as exc:
        log.error("get_message failed: %s", exc)
        return None


async def mark_read(
    token_file: str,
    credentials_file: str,
    message_id: str,
    account_name: str = "primary",
) -> bool:
    def _do():
        svc = _get_service(account_name, token_file, credentials_file)
        svc.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        return True

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _do)
    except Exception as exc:
        log.error("mark_read failed: %s", exc)
        return False


async def send_reply(
    token_file: str,
    credentials_file: str,
    message_id: str,
    reply_body: str,
    account_name: str = "primary",
    from_email: str = "",
) -> bool:
    """Reply to an existing message, keeping the thread."""
    def _do():
        svc = _get_service(account_name, token_file, credentials_file)
        original = svc.users().messages().get(
            userId="me", id=message_id, format="metadata",
            metadataHeaders=["Subject", "From", "To", "Message-ID", "References"],
        ).execute()
        payload = original.get("payload", {})
        headers = payload.get("headers", [])
        thread_id = original.get("threadId", "")

        orig_subject = _header(headers, "subject")
        orig_from = _header(headers, "from")
        orig_msg_id = _header(headers, "message-id")
        orig_refs = _header(headers, "references")

        subject = orig_subject if orig_subject.lower().startswith("re:") else f"Re: {orig_subject}"
        references = f"{orig_refs} {orig_msg_id}".strip() if orig_refs else orig_msg_id

        msg = email_lib.mime.text.MIMEText(reply_body)
        msg["to"] = orig_from
        msg["subject"] = subject
        msg["in-reply-to"] = orig_msg_id
        msg["references"] = references
        if from_email:
            msg["from"] = from_email

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        svc.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id},
        ).execute()
        return True

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _do)
    except Exception as exc:
        log.error("send_reply failed: %s", exc)
        return False


async def send_email(
    token_file: str,
    credentials_file: str,
    to: str,
    subject: str,
    body: str,
    account_name: str = "primary",
    from_email: str = "",
) -> bool:
    """Send a new email."""
    def _do():
        svc = _get_service(account_name, token_file, credentials_file)
        msg = email_lib.mime.text.MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject
        if from_email:
            msg["from"] = from_email
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        svc.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()
        return True

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _do)
    except Exception as exc:
        log.error("send_email failed: %s", exc)
        return False


def format_inbox_text(messages: list[dict], account_name: str = "") -> str:
    if not messages:
        prefix = f"{account_name}: " if account_name else ""
        return f"{prefix}No unread messages."
    lines = [f"Inbox ({account_name or 'account'}) -- {len(messages)} unread:"]
    for m in messages:
        date_str = m["date"].strftime("%b %d %H:%M") if m.get("date") else "?"
        sender = m.get("from", "?").split("<")[0].strip()[:25]
        subj = (m.get("subject") or "(no subject)")[:55]
        lines.append(f"  [{m['id'][:8]}]  {date_str}  {sender}  {subj}")
    return "\n".join(lines)
