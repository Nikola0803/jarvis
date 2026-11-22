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
from mcp.server.mcpserver import MCPServer

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

mcp = MCPServer("jarvis")


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


# ── Reminders / tasks ────────────────────────────────────────────────────────

@mcp.tool()
async def add_reminder(text: str, due_iso: str = "", project: str = "") -> str:
    """Add a reminder or task for the owner. `due_iso` is an ISO-8601 datetime
    (e.g. 2026-11-25T15:00:00) or empty for no due time. `project` optionally
    tags it to a project. Convert natural language like 'Tuesday 3pm' to ISO
    yourself before calling."""
    from hermes.db.queries import add_reminder as _add
    rid = await _add(_DB_PATH, text, due_at=(due_iso or None), project=(project or None))
    return f"Reminder #{rid} saved" + (f" (due {due_iso})" if due_iso else "") + "."


@mcp.tool()
async def list_reminders(include_done: bool = False) -> str:
    """List the owner's open reminders/tasks (soonest due first)."""
    from hermes.db.queries import list_reminders as _list
    rows = await _list(_DB_PATH, include_done=include_done)
    if not rows:
        return "No open reminders."
    out = []
    for r in rows:
        due = f" — due {r['due_at']}" if r.get("due_at") else ""
        proj = f" [{r['project']}]" if r.get("project") else ""
        mark = "x" if r.get("done") else " "
        out.append(f"#{r['id']} [{mark}]{proj} {r['text']}{due}")
    return "\n".join(out)


@mcp.tool()
async def complete_reminder(reminder_id: int) -> str:
    """Mark a reminder/task done by its id."""
    from hermes.db.queries import complete_reminder as _done
    ok = await _done(_DB_PATH, reminder_id)
    return "Done." if ok else "No open reminder with that id."


# ── GitHub / projects ────────────────────────────────────────────────────────

_GH = _CFG.get("github") or {}
_GH_TOKEN = _GH.get("token", "")
_GH_REPOS = _GH.get("repos") or []
_GH_MAX = _GH.get("max_shown", 8)


@mcp.tool()
async def list_github_projects(limit: int = 8) -> str:
    """List the owner's GitHub repos (most recently active first) with how many
    issues/PRs are open on each. Use for 'what am I working on' questions."""
    if not _GH_TOKEN:
        return "GitHub is not configured."
    from hermes.integrations import github as gh
    try:
        repos = await gh.list_repos(_GH_TOKEN, limit=limit, repos=_GH_REPOS or None)
    except Exception as exc:  # noqa: BLE001
        return f"GitHub error: {exc}"
    if not repos:
        return "No repositories found."
    out = []
    for r in repos:
        out.append(f"{r['name']} — {r['open_issues']} open — pushed {r.get('pushed_at', '')[:10]}"
                   + (f" — {r['description']}" if r.get("description") else ""))
    return "\n".join(out)


@mcp.tool()
async def list_repo_issues(repo: str, limit: int = 10) -> str:
    """List open issues for one repo, given as 'owner/name'."""
    if not _GH_TOKEN:
        return "GitHub is not configured."
    from hermes.integrations import github as gh
    try:
        issues = await gh.list_open_issues(_GH_TOKEN, repo, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return f"GitHub error: {exc}"
    if not issues:
        return f"No open issues in {repo}."
    return "\n".join(f"#{i['number']} {i['title']}"
                     + (f" [{', '.join(i['labels'])}]" if i.get("labels") else "")
                     for i in issues)


@mcp.tool()
async def list_repo_pull_requests(repo: str, limit: int = 10) -> str:
    """List open pull requests for one repo, given as 'owner/name'."""
    if not _GH_TOKEN:
        return "GitHub is not configured."
    from hermes.integrations import github as gh
    try:
        prs = await gh.list_open_pull_requests(_GH_TOKEN, repo, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return f"GitHub error: {exc}"
    if not prs:
        return f"No open pull requests in {repo}."
    return "\n".join(f"#{p['number']} {p['title']}" + (" (draft)" if p.get("draft") else "")
                     + (f" — @{p['author']}" if p.get("author") else "")
                     for p in prs)


# ── Codex: delegate coding/design work ───────────────────────────────────────

_CODEX = _CFG.get("codex") or {}
_CODEX_ENABLED = _CODEX.get("enabled", False)
_CODEX_BIN = _CODEX.get("binary", "codex")
_CODEX_DEFAULT_REPO = _CODEX.get("default_repo", "/opt/jarvis")
_CODEX_ALLOWED = _CODEX.get("allowed_repos") or [_CODEX_DEFAULT_REPO]
_CODEX_EXTRA = _CODEX.get("extra_args") or []
_CODEX_TIMEOUT = _CODEX.get("timeout_seconds", 900)


def _repo_allowed(path: str) -> bool:
    rp = os.path.realpath(path)
    for allowed in _CODEX_ALLOWED:
        ap = os.path.realpath(allowed)
        if rp == ap or rp.startswith(ap + os.sep):
            return True
    return False


@mcp.tool()
async def code_task(instruction: str, repo: str = "", timeout_seconds: int = 0) -> str:
    """Delegate a CODING or DESIGN task to Codex (OpenAI's coding agent) running
    on this server. Codex edits files in the given repo and returns a summary of
    what it changed. `repo` is an absolute path to one of the allowed repos
    (default: the main repo). Use this to write or change code, markup or styles
    -- not for ordinary conversation. Describe the change clearly and concretely."""
    import asyncio
    if not _CODEX_ENABLED:
        return ("Codex is not enabled. Set codex.enabled: true in config and run "
                "`codex` once on the server to sign in with ChatGPT.")
    repo_path = repo or _CODEX_DEFAULT_REPO
    if not _repo_allowed(repo_path):
        return f"Repo '{repo_path}' is not in codex.allowed_repos."
    if not os.path.isdir(repo_path):
        return f"Repo path '{repo_path}' does not exist."
    timeout = timeout_seconds or _CODEX_TIMEOUT
    args = [_CODEX_BIN, "exec", "--cd", repo_path, *(_CODEX_EXTRA), instruction]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return f"Codex timed out after {timeout}s (the task may be too large)."
    except FileNotFoundError:
        return f"Codex binary '{_CODEX_BIN}' not found on PATH."
    except Exception as exc:  # noqa: BLE001
        return f"Codex error: {exc}"
    text = (out or b"").decode(errors="replace").strip()
    if not text:
        return "Codex finished with no output."
    # Return the tail: Codex prints its working steps then a final summary.
    return text[-3500:]


# ── Claude Code: a second coding hand (multi-account) ────────────────────────

_CC = _CFG.get("claude_code") or {}
_CC_ENABLED = _CC.get("enabled", False)
_CC_BIN = _CC.get("binary", "claude")
_CC_DEFAULT_REPO = _CC.get("default_repo", "/opt/jarvis")
_CC_ALLOWED = _CC.get("allowed_repos") or [_CC_DEFAULT_REPO]
_CC_ACCOUNTS = {a["name"]: a for a in (_CC.get("accounts") or []) if a.get("name")}
_CC_EXTRA = _CC.get("extra_args") or ["--permission-mode", "acceptEdits"]
_CC_TIMEOUT = _CC.get("timeout_seconds", 900)


def _cc_repo_allowed(path: str) -> bool:
    rp = os.path.realpath(path)
    for allowed in _CC_ALLOWED:
        ap = os.path.realpath(allowed)
        if rp == ap or rp.startswith(ap + os.sep):
            return True
    return False


@mcp.tool()
async def code_task_claude(instruction: str, repo: str = "", account: str = "",
                           timeout_seconds: int = 0) -> str:
    """Delegate a CODING or DESIGN task to Claude Code (a second coding hand next
    to Codex). It edits files in the given repo and returns a summary. `repo` is
    an absolute path in the allowed list (default: the main repo). `account`
    picks which Claude login to use (by name) when more than one is configured.
    Use for writing/changing code; describe the change clearly."""
    import asyncio
    if not _CC_ENABLED:
        return ("Claude Code is not enabled. Set claude_code.enabled: true and run "
                "`claude` once on the server to sign in.")
    repo_path = repo or _CC_DEFAULT_REPO
    if not _cc_repo_allowed(repo_path):
        return f"Repo '{repo_path}' is not in claude_code.allowed_repos."
    if not os.path.isdir(repo_path):
        return f"Repo path '{repo_path}' does not exist."

    env = dict(os.environ)
    if account:
        acct = _CC_ACCOUNTS.get(account)
        if not acct:
            return f"Unknown Claude account '{account}'. Configured: {', '.join(_CC_ACCOUNTS) or 'none'}."
        if acct.get("config_dir"):
            env["CLAUDE_CONFIG_DIR"] = acct["config_dir"]
    elif _CC_ACCOUNTS:
        # default to the first configured account's auth
        first = next(iter(_CC_ACCOUNTS.values()))
        if first.get("config_dir"):
            env["CLAUDE_CONFIG_DIR"] = first["config_dir"]

    timeout = timeout_seconds or _CC_TIMEOUT
    args = [_CC_BIN, "-p", *(_CC_EXTRA), instruction]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=repo_path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return f"Claude Code timed out after {timeout}s (task may be too large)."
    except FileNotFoundError:
        return f"Claude binary '{_CC_BIN}' not found on PATH."
    except Exception as exc:  # noqa: BLE001
        return f"Claude Code error: {exc}"
    text = (out or b"").decode(errors="replace").strip()
    return text[-3500:] if text else "Claude Code finished with no output."


if __name__ == "__main__":
    mcp.run()
