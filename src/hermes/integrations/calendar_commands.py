"""Calendar command handler.

Intercepts messages that look like calendar requests before they reach the LLM.
Supports both explicit commands and natural language:

Explicit:
  /cal today         -- today's events
  /cal tomorrow      -- tomorrow's events
  /cal week          -- next 7 days
  /cal add <details> -- create event
  /cal find [mins]   -- find free slot
  /cal auth [name]   -- start Google OAuth2
  /cal auth_code <code>  -- complete OAuth2

Natural (if _NATURAL_DETECT is True):
  "what's on my calendar today"
  "am I free Thursday at 3pm"
  "schedule a call with John tomorrow 2pm MST"
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

_NATURAL_DETECT = True

_CAL_KEYWORDS = re.compile(
    r"\b(calendar|schedule|meeting|call|event|appointment|"
    r"what(?:'s| is) on|am i free|are you free|"
    r"book a|set up a|arrange|reschedule|cancel event|"
    r"free slot|available|busy)\b",
    re.IGNORECASE,
)

# Pending auth flows: account_name -> flow object
_pending_flows: dict[str, Any] = {}


def is_calendar_intent(text: str) -> bool:
    """Quick check: should this message be handled by the calendar module?"""
    low = text.strip().lower()
    if low.startswith("/cal"):
        return True
    if _NATURAL_DETECT and _CAL_KEYWORDS.search(text):
        return True
    return False


async def handle(
    text: str,
    cfg,         # CalendarConfig from config.py
    user_tz: str = "Europe/Belgrade",
) -> str | None:
    """Process a calendar-related message. Returns reply string or None if
    this handler can't process it (fall through to LLM)."""
    from .timezone_utils import multi_tz_display, parse_time_text, resolve, tz_summary
    from .google_calendar import (
        list_upcoming, create_event, find_free_slots, format_events_text,
        start_auth_flow, finish_auth_flow,
    )

    if cfg is None:
        return None

    low = text.strip().lower()
    parts = text.strip().split(None, 2)

    # ── /cal auth [account] ─────────────────────────────────────────────
    if low.startswith("/cal auth_code"):
        code = text.split(None, 2)[2] if len(parts) >= 3 else ""
        # find pending flow
        for acct_name, flow in list(_pending_flows.items()):
            try:
                email = finish_auth_flow(flow, code, _token_path(cfg, acct_name))
                del _pending_flows[acct_name]
                return f"Google Calendar authenticated for {email} (account: {acct_name}). Try /cal today."
            except Exception as e:
                return f"Auth failed: {e}"
        return "No pending auth flow. Run /cal auth first."

    if low.startswith("/cal auth"):
        acct = parts[2] if len(parts) >= 3 else "primary"
        try:
            url, flow = start_auth_flow(cfg.credentials_file)
            _pending_flows[acct] = flow
            return (
                f"Open this URL to authorize Google Calendar (account: {acct}):\n\n"
                f"{url}\n\n"
                f"After approving, paste the code back with:\n"
                f"/cal auth_code <paste-code-here>"
            )
        except Exception as e:
            return f"Could not start auth: {e}"

    # ── Determine account ───────────────────────────────────────────────
    # Default to first configured account
    accounts = cfg.accounts or []
    if not accounts:
        return "No Google Calendar accounts configured. Add an account in config.yaml and run /cal auth."

    primary = accounts[0]
    token_file = primary.token_file
    creds_file = cfg.credentials_file
    acct_name  = primary.name

    # ── /cal today / tomorrow / week ───────────────────────────────────
    if low.startswith("/cal today") or ("what" in low and "today" in low):
        events = await list_upcoming(token_file, creds_file, hours=24, user_tz=user_tz, account_name=acct_name)
        lines = format_events_text(events, user_tz)
        return f"TODAY ({acct_name}):\n{lines}"

    if low.startswith("/cal tomorrow") or "tomorrow" in low and "calendar" in low:
        # offset to 24-48h window -- approximate via hour window
        events = await list_upcoming(token_file, creds_file, hours=48, user_tz=user_tz, account_name=acct_name)
        from datetime import date
        tomorrow = (datetime.now() + timedelta(days=1)).date()
        events = [e for e in events if e["start"] and e["start"].date() == tomorrow]
        lines = format_events_text(events, user_tz)
        return f"TOMORROW ({acct_name}):\n{lines}"

    if low.startswith("/cal week") or "this week" in low:
        events = await list_upcoming(token_file, creds_file, hours=168, max_results=25, user_tz=user_tz, account_name=acct_name)
        lines = format_events_text(events, user_tz)
        return f"NEXT 7 DAYS ({acct_name}):\n{lines}"

    # ── /cal find [duration_mins] ───────────────────────────────────────
    if low.startswith("/cal find"):
        mins_match = re.search(r"(\d+)\s*(?:min|m)", low)
        mins = int(mins_match.group(1)) if mins_match else 60
        slots = await find_free_slots(token_file, creds_file, duration_minutes=mins, account_name=acct_name)
        if not slots:
            return "No free slots found in the next 7 days."
        lines = []
        for s in slots:
            lines.append(f"  {s.strftime('%a %b %d, %H:%M')} Belgrade\n  {tz_summary(s)}")
        return f"Free {mins}-min slots ({acct_name}):\n\n" + "\n\n".join(lines)

    # ── /cal add <title> [date] [time] [tz] ────────────────────────────
    if low.startswith("/cal add"):
        body = text[len("/cal add"):].strip()
        # Try to parse a time expression from the body
        dt = parse_time_text(body)
        if not dt:
            return (
                "Could not parse a date/time. Try:\n"
                "/cal add Team call tomorrow 3pm\n"
                "/cal add Client meeting Monday 2pm MST"
            )
        title_match = re.match(r"^([^0-9/]+?)(?:\s+(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next|at|\d))", body, re.IGNORECASE)
        title = title_match.group(1).strip() if title_match else body.split("  ")[0][:60]
        try:
            event = await create_event(token_file, creds_file, title=title, start=dt, account_name=acct_name)
            return (
                f"Event created: {title}\n"
                f"{multi_tz_display(dt)}\n"
                f"Link: {event.get('htmlLink','')}"
            )
        except Exception as e:
            return f"Failed to create event: {e}"

    # ── Natural: "schedule a call with John tomorrow 3pm MST" ─────────
    schedule_match = re.search(
        r"(?:schedule|book|set up|arrange|create)\s+(?:a\s+)?(.+?)"
        r"(?:\s+(?:with\s+\w+\s+)?(?:on\s+|for\s+)?)((?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next\s+\w+)\s+\w+)",
        low, re.IGNORECASE
    )
    if schedule_match:
        what = schedule_match.group(1).strip()
        when_text = schedule_match.group(2).strip()
        dt = parse_time_text(when_text)
        if dt:
            try:
                event = await create_event(token_file, creds_file, title=what.title(), start=dt, account_name=acct_name)
                return (
                    f"Scheduled: {what.title()}\n"
                    f"{multi_tz_display(dt)}\n"
                    f"Link: {event.get('htmlLink','')}"
                )
            except Exception as e:
                return f"Failed: {e}"

    # ── Natural: "am I free Thursday 3pm" ──────────────────────────────
    if re.search(r"\b(?:am i|are you)\s+free\b", low):
        dt = parse_time_text(text)
        if dt:
            events = await list_upcoming(token_file, creds_file, hours=168, user_tz=user_tz, account_name=acct_name)
            end_dt = dt + timedelta(hours=1)
            conflicts = [
                e for e in events
                if e["start"] and e["start"] < end_dt and (e["end"] or e["start"] + timedelta(hours=1)) > dt
            ]
            if conflicts:
                return f"Busy at {dt.strftime('%a %b %d %H:%M')} -- {conflicts[0]['title']}"
            return f"Free at {dt.strftime('%a %b %d %H:%M')} Belgrade. {tz_summary(dt)}"

    # ── Timezone quick-convert: "what time is 3pm MST in Belgrade" ─────
    tz_question = re.search(r"what(?:\s+is|\s*'s)?\s+(?:the\s+)?time", low)
    if tz_question or re.search(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)\s+\w+\s+(?:in|for)\s+\w+", low, re.IGNORECASE):
        dt = parse_time_text(text)
        if dt:
            return multi_tz_display(dt)

    # Couldn't handle it specifically -- return None to fall through to LLM
    return None


def _token_path(cfg, account_name: str) -> str:
    for acct in (cfg.accounts or []):
        if acct.name == account_name:
            return acct.token_file
    # Default path
    return f"/home/hermes/.hermes/google_token_{account_name}.json"
