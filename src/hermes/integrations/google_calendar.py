"""Google Calendar integration.

Multi-account support: each account has a name + token file path.
Initial OAuth2 uses the out-of-band flow so the user can auth via
Telegram without needing a local browser on the VPS.

Auth flow:
  1. User: /cal auth [account]
  2. Jarvis: "Open this URL, approve, paste the code back."
  3. User: /cal auth_code [code]
  4. Jarvis saves token, confirms.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

# Lazily imported so the module loads even when google libs aren't installed
_service_cache: dict[str, Any] = {}


def _build_service(token_file: str, credentials_file: str):
    """Build a Google Calendar service from stored token."""
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
                "Run /cal auth to authenticate."
            )

    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return svc


def _get_service(account_name: str, token_file: str, credentials_file: str):
    key = f"{account_name}:{token_file}"
    if key not in _service_cache:
        _service_cache[key] = _build_service(token_file, credentials_file)
    return _service_cache[key]


# ── OAuth2 helpers ─────────────────────────────────────────────────────────

def start_auth_flow(credentials_file: str) -> tuple[str, Any]:
    """Begin OAuth2 flow. Returns (auth_url, flow) for the user to open."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account consent",
    )
    return auth_url, flow


def finish_auth_flow(flow, code: str, token_file: str) -> str:
    """Complete OAuth2 with the user-pasted code. Returns account email."""
    flow.fetch_token(code=code.strip())
    creds = flow.credentials
    Path(token_file).parent.mkdir(parents=True, exist_ok=True)
    with open(token_file, "w") as f:
        f.write(creds.to_json())
    _service_cache.clear()
    # Extract email from id_token if present
    try:
        import jwt as pyjwt
        payload = pyjwt.decode(creds.id_token, options={"verify_signature": False})
        return payload.get("email", "account")
    except Exception:
        return "account"


# ── Calendar operations ────────────────────────────────────────────────────

async def list_upcoming(
    token_file: str,
    credentials_file: str,
    hours: int = 24,
    max_results: int = 10,
    user_tz: str = "Europe/Belgrade",
    account_name: str = "primary",
) -> list[dict]:
    """Return upcoming events within the next `hours` hours."""
    def _fetch():
        svc = _get_service(account_name, token_file, credentials_file)
        now = datetime.utcnow()
        end = now + timedelta(hours=hours)
        result = svc.events().list(
            calendarId="primary",
            timeMin=now.isoformat() + "Z",
            timeMax=end.isoformat() + "Z",
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        return result.get("items", [])

    events = await asyncio.get_event_loop().run_in_executor(None, _fetch)
    return [_format_event(e, user_tz) for e in events]


async def create_event(
    token_file: str,
    credentials_file: str,
    title: str,
    start: datetime,
    end: datetime | None = None,
    description: str = "",
    location: str = "",
    account_name: str = "primary",
) -> dict:
    """Create a Google Calendar event."""
    if end is None:
        end = start + timedelta(hours=1)

    def _create():
        svc = _get_service(account_name, token_file, credentials_file)
        tz_str = str(start.tzinfo) if start.tzinfo else "Europe/Belgrade"
        body = {
            "summary": title,
            "description": description,
            "location": location,
            "start": {"dateTime": start.isoformat(), "timeZone": tz_str},
            "end":   {"dateTime": end.isoformat(),   "timeZone": tz_str},
        }
        return svc.events().insert(calendarId="primary", body=body).execute()

    event = await asyncio.get_event_loop().run_in_executor(None, _create)
    return event


async def find_free_slots(
    token_file: str,
    credentials_file: str,
    duration_minutes: int = 60,
    look_ahead_days: int = 7,
    account_name: str = "primary",
) -> list[datetime]:
    """Find free slots in the calendar over the next `look_ahead_days` days."""
    def _fetch():
        svc = _get_service(account_name, token_file, credentials_file)
        now = datetime.utcnow()
        end = now + timedelta(days=look_ahead_days)
        result = svc.freebusy().query(body={
            "timeMin": now.isoformat() + "Z",
            "timeMax": end.isoformat() + "Z",
            "items": [{"id": "primary"}],
        }).execute()
        busy = result["calendars"]["primary"].get("busy", [])
        return busy

    busy = await asyncio.get_event_loop().run_in_executor(None, _fetch)

    # Simple slot finder: check each working hour window
    from zoneinfo import ZoneInfo
    owner_tz = ZoneInfo("Europe/Belgrade")
    slots = []
    candidate = datetime.now(owner_tz).replace(minute=0, second=0, microsecond=0)
    candidate += timedelta(hours=1)
    end_limit = candidate + timedelta(days=look_ahead_days)

    busy_ranges = [(
        datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
        datetime.fromisoformat(b["end"].replace("Z", "+00:00")),
    ) for b in busy]

    while candidate < end_limit and len(slots) < 5:
        # Only suggest 9am-7pm Belgrade
        local = candidate.astimezone(owner_tz)
        if 9 <= local.hour < 19:
            slot_end = candidate + timedelta(minutes=duration_minutes)
            free = all(
                not (s < slot_end and e > candidate)
                for s, e in busy_ranges
            )
            if free:
                slots.append(candidate)
        candidate += timedelta(hours=1)

    return slots


def _format_event(event: dict, user_tz: str) -> dict:
    """Normalize a Google Calendar event to a simple dict."""
    start = event.get("start", {})
    end   = event.get("end",   {})

    def _parse_dt(d: dict):
        if "dateTime" in d:
            return datetime.fromisoformat(d["dateTime"])
        if "date" in d:
            return datetime.fromisoformat(d["date"])
        return None

    start_dt = _parse_dt(start)
    end_dt   = _parse_dt(end)

    if start_dt and start_dt.tzinfo:
        start_local = start_dt.astimezone(ZoneInfo(user_tz))
    else:
        start_local = start_dt

    return {
        "id":          event.get("id", ""),
        "title":       event.get("summary", "(no title)"),
        "start":       start_local,
        "end":         end_dt,
        "location":    event.get("location", ""),
        "description": event.get("description", ""),
        "link":        event.get("htmlLink", ""),
        "attendees":   [a.get("email", "") for a in event.get("attendees", [])],
    }


def format_events_text(events: list[dict], user_tz: str = "Europe/Belgrade") -> str:
    if not events:
        return "No upcoming events."
    lines = []
    for e in events:
        s = e["start"]
        time_str = s.strftime("%a %b %d, %H:%M") if s else "all day"
        line = f"  {time_str}  {e['title']}"
        if e["location"]:
            line += f"  @ {e['location']}"
        lines.append(line)
    return "\n".join(lines)
