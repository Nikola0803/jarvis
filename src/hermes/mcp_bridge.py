#!/usr/bin/env python3
"""MCP bridge: exposes Hermes/Jarvis's own Gmail, Calendar and memory as MCP
tools so Nous Research's Hermes Agent (the real agentic harness) can call them.

This reuses the EXISTING OAuth tokens and SQLite memory already set up for the
custom Hermes backend -- no re-authentication. It is launched by Hermes Agent as
a stdio MCP server, configured in ~/.hermes/config.yaml:

    mcp_servers:
      jarvis:
        command: "/opt/jarvis/venv/bin/python"
        args: ["/opt/jarvis/src/hermes/mcp_bridge.py"]
        env:
          HERMES_CONFIG: "/etc/hermes/config.yaml"

Requires the `mcp` package in that venv:  /opt/jarvis/venv/bin/pip install mcp
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

# Make the hermes package importable when launched as a bare script.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import yaml
from mcp.server.fastmcp import FastMCP

from hermes.integrations import gmail, google_calendar

CONFIG_PATH = os.getenv("HERMES_CONFIG", "/etc/hermes/config.yaml")


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


_CFG = _load_config()
_GMAIL = _CFG.get("gmail") or {}
_GMAIL_CREDS = _GMAIL.get("credentials_file", "/etc/hermes/google_credentials.json")
_GMAIL_ACCOUNTS = {a["name"]: a for a in _GMAIL.get("accounts", [])}
_CAL = _CFG.get("calendar") or {}
_CAL_CREDS = _CAL.get("credentials_file", "/etc/hermes/google_credentials.json")
_CAL_ACCOUNTS = _CAL.get("accounts", [])
_CAL_TZ = _CAL.get("timezone", "Europe/Belgrade")
_DB_PATH = (_CFG.get("database") or {}).get("path", "/home/hermes/.hermes/hermes.db")

mcp = FastMCP("jarvis")


def _account(name: str) -> dict | None:
    return _GMAIL_ACCOUNTS.get(name)


# ── Email ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def list_unread_emails(account: str = "all", limit: int = 10) -> str:
    """List unread emails. `account` is one of the configured account names
    (e.g. 'paywise', 'velocity', 'personal') or 'all' for every account.
    Returns id, account, from, subject and a snippet for each message."""
    names = list(_GMAIL_ACCOUNTS) if account == "all" else [account]
    out: list[str] = []
    for name in names:
        acct = _account(name)
        if not acct:
            out.append(f"[{name}] unknown account")
            continue
        try:
            msgs = await gmail.list_messages(
                token_file=acct["token_file"],
                credentials_file=_GMAIL_CREDS,
                account_name=name,
                unread_only=True,
                max_results=limit,
            )
        except Exception as exc:  # noqa: BLE001
            out.append(f"[{name}] error: {exc}")
            continue
        if not msgs:
            out.append(f"[{name}] no unread email")
            continue
        for m in msgs:
            out.append(
                f"[{name}] id={m.get('id')} | {(m.get('from') or '').split('<')[0].strip()} "
                f"| {m.get('subject') or '(no subject)'} | {(m.get('snippet') or '')[:120]}"
            )
    return "\n".join(out) or "No unread email."


@mcp.tool()
async def read_email(account: str, message_id: str) -> str:
    """Read the full body of one email by its id, for the given account."""
    acct = _account(account)
    if not acct:
        return f"Unknown account '{account}'."
    msg = await gmail.get_message(
        token_file=acct["token_file"],
        credentials_file=_GMAIL_CREDS,
        message_id=message_id,
        account_name=account,
    )
    if not msg:
        return "Message not found."
    return (
        f"From: {msg.get('from')}\nSubject: {msg.get('subject')}\n"
        f"Date: {msg.get('date')}\n\n{msg.get('body') or msg.get('snippet') or ''}"
    )


@mcp.tool()
async def send_email(account: str, to: str, subject: str, body: str) -> str:
    """Send a new email FROM one of the configured accounts."""
    acct = _account(account)
    if not acct:
        return f"Unknown account '{account}'."
    ok = await gmail.send_email(
        token_file=acct["token_file"],
        credentials_file=_GMAIL_CREDS,
        to=to,
        subject=subject,
        body=body,
        account_name=account,
        from_email=acct.get("email", ""),
    )
    return "Sent." if ok else "Send failed."


@mcp.tool()
async def reply_email(account: str, message_id: str, body: str) -> str:
    """Reply to an existing email in its thread, from the given account."""
    acct = _account(account)
    if not acct:
        return f"Unknown account '{account}'."
    ok = await gmail.send_reply(
        token_file=acct["token_file"],
        credentials_file=_GMAIL_CREDS,
        message_id=message_id,
        reply_body=body,
        account_name=account,
        from_email=acct.get("email", ""),
    )
    return "Reply sent." if ok else "Reply failed."


# ── Calendar ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def upcoming_events(hours: int = 24) -> str:
    """List calendar events in the next `hours` hours across configured calendars."""
    if not _CAL_ACCOUNTS:
        return "No calendar configured."
    out: list[str] = []
    for acct in _CAL_ACCOUNTS:
        try:
            events = await google_calendar.list_upcoming(
                token_file=acct["token_file"],
                credentials_file=_CAL_CREDS,
                hours=hours,
                user_tz=_CAL_TZ,
                account_name=acct.get("name", "primary"),
            )
        except Exception as exc:  # noqa: BLE001
            out.append(f"[{acct.get('name')}] error: {exc}")
            continue
        for e in events:
            out.append(f"[{acct.get('name')}] {e.get('start')} — {e.get('title')}"
                       + (f" @ {e.get('location')}" if e.get("location") else ""))
    return "\n".join(out) or "Nothing on the calendar."


@mcp.tool()
async def create_calendar_event(title: str, start_iso: str, duration_minutes: int = 60,
                                 description: str = "", location: str = "") -> str:
    """Create a calendar event. start_iso is ISO-8601 (e.g. 2026-11-23T15:00:00)."""
    if not _CAL_ACCOUNTS:
        return "No calendar configured."
    acct = _CAL_ACCOUNTS[0]
    try:
        start = datetime.fromisoformat(start_iso)
    except ValueError:
        return "start_iso must be ISO-8601, e.g. 2026-11-23T15:00:00"
    event = await google_calendar.create_event(
        token_file=acct["token_file"],
        credentials_file=_CAL_CREDS,
        title=title,
        start=start,
        end=start + timedelta(minutes=duration_minutes),
        description=description,
        location=location,
        account_name=acct.get("name", "primary"),
    )
    return f"Created: {event.get('htmlLink', event.get('id', 'ok'))}"


# ── Long-term memory (profile + facts) ───────────────────────────────────────

@mcp.tool()
def recall_memory(query: str = "") -> str:
    """Recall stored facts/profile about the owner. Empty query = everything known."""
    from hermes.memory import get_store
    store = get_store(_DB_PATH)
    lines: list[str] = []
    profile = store.all_profile()
    if profile:
        lines.append("Profile: " + "; ".join(f"{k}={v}" for k, v in profile.items()))
    facts = store.search_facts(query) if query else store.recent_facts(limit=15)
    for f in facts:
        lines.append(f"- {f['subject']} {f['relation']} {f['value']}")
    return "\n".join(lines) or "Nothing stored yet."


@mcp.tool()
def remember_fact(subject: str, relation: str, value: str) -> str:
    """Store a durable fact about the owner or their business (e.g. subject='Ivan',
    relation='is', value='main client, reply fast')."""
    from hermes.memory import get_store
    store = get_store(_DB_PATH)
    store.add_fact(subject, relation, value, source="hermes-agent")
    return "Remembered."


if __name__ == "__main__":
    mcp.run()
