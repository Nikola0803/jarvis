"""Gmail command handler.

Explicit commands:
  /mail inbox [account]        -- unread emails (default: all configured accounts)
  /mail read <msg_id>          -- read a full message
  /mail reply <msg_id> <text>  -- reply to message
  /mail send <to> | <subject> | <body>
  /mail mark_read <msg_id>     -- mark as read
  /mail auth [account]         -- start OAuth2
  /mail auth_code <code>       -- complete OAuth2

Natural language detection (auto-routed before LLM):
  "any new emails", "check my inbox", "what's in my email"
  "reply to [name] say [text]"
"""
from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_pending_flows: dict[str, Any] = {}

_MAIL_KEYWORDS = re.compile(
    r"\b(email|inbox|mail|message|unread|reply|newsletter|"
    r"what(?:'s| is) in my|check my (email|mail|inbox))\b",
    re.IGNORECASE,
)


def is_mail_intent(text: str) -> bool:
    low = text.strip().lower()
    if low.startswith("/mail"):
        return True
    if _MAIL_KEYWORDS.search(text):
        return True
    return False


async def handle(
    text: str,
    cfg,           # GmailConfig from config.py
    user_tz: str = "Europe/Belgrade",
) -> str | None:
    """Process a mail-related message. Returns reply string or None to fall through."""
    from .gmail import (
        list_messages, get_message, mark_read, send_reply, send_email,
        format_inbox_text, start_auth_flow, finish_auth_flow,
    )

    if cfg is None:
        return None

    low = text.strip().lower()
    parts = text.strip().split(None, 2)

    creds_file = cfg.credentials_file

    # ── /mail auth [account] ───────────────────────────────────────────
    if low.startswith("/mail auth_code"):
        code = parts[2] if len(parts) >= 3 else ""
        for acct_name, flow in list(_pending_flows.items()):
            try:
                token_file = _token_path(cfg, acct_name)
                email_addr = finish_auth_flow(flow, code, token_file)
                del _pending_flows[acct_name]
                return f"Gmail authenticated for {email_addr} (account: {acct_name}). Try /mail inbox."
            except Exception as exc:
                return f"Auth failed: {exc}"
        return "No pending auth flow. Run /mail auth first."

    if low.startswith("/mail auth"):
        acct = parts[2] if len(parts) >= 3 else (cfg.accounts[0].name if cfg.accounts else "primary")
        try:
            url, flow = start_auth_flow(creds_file)
            _pending_flows[acct] = flow
            return (
                f"Open this URL to authorize Gmail (account: {acct}):\n\n"
                f"{url}\n\n"
                f"After approving, paste the code with:\n"
                f"/mail auth_code <code>"
            )
        except Exception as exc:
            return f"Could not start auth: {exc}"

    # ── /mail inbox [account] ──────────────────────────────────────────
    if low.startswith("/mail inbox") or _is_inbox_check(low):
        target_acct = None
        if low.startswith("/mail inbox") and len(parts) >= 3:
            target_acct = parts[2].strip()

        accounts_to_check = []
        if target_acct:
            acct = _find_account(cfg, target_acct)
            if acct:
                accounts_to_check = [acct]
            else:
                return f"Account '{target_acct}' not configured. Available: {', '.join(a.name for a in cfg.accounts)}"
        else:
            accounts_to_check = cfg.accounts or []

        if not accounts_to_check:
            return "No Gmail accounts configured. Run /mail auth to set one up."

        sections = []
        for acct in accounts_to_check:
            try:
                msgs = await list_messages(
                    _token_path(cfg, acct.name), creds_file,
                    account_name=acct.name, unread_only=True, max_results=5,
                )
                sections.append(format_inbox_text(msgs, acct.name))
            except RuntimeError as exc:
                sections.append(f"{acct.name}: not authenticated -- run /mail auth {acct.name}")
            except Exception as exc:
                sections.append(f"{acct.name}: error -- {exc}")

        return "\n\n".join(sections)

    # ── /mail read <id> ───────────────────────────────────────────────
    if low.startswith("/mail read"):
        msg_id = parts[2] if len(parts) >= 3 else ""
        if not msg_id:
            return "Usage: /mail read <message_id>"
        # Try each account to find the message
        for acct in (cfg.accounts or []):
            try:
                msg = await get_message(_token_path(cfg, acct.name), creds_file, msg_id, acct.name)
                if msg:
                    await mark_read(_token_path(cfg, acct.name), creds_file, msg_id, acct.name)
                    lines = [
                        f"From: {msg['from']}",
                        f"Subject: {msg['subject']}",
                        f"Date: {msg['date'].strftime('%b %d %H:%M') if msg.get('date') else '?'}",
                        "",
                        msg["body"][:800] or msg["snippet"],
                    ]
                    return "\n".join(lines)
            except Exception:
                continue
        return f"Message {msg_id} not found in any account."

    # ── /mail reply <id> <text> ────────────────────────────────────────
    if low.startswith("/mail reply"):
        raw = text.strip()[len("/mail reply"):].strip()
        # Format: <id> <reply text>
        m = re.match(r"(\S+)\s+(.*)", raw, re.DOTALL)
        if not m:
            return "Usage: /mail reply <message_id> <reply text>"
        msg_id, reply_text = m.group(1), m.group(2).strip()
        for acct in (cfg.accounts or []):
            try:
                ok = await send_reply(
                    _token_path(cfg, acct.name), creds_file, msg_id, reply_text,
                    acct.name, acct.email,
                )
                if ok:
                    return f"Reply sent from {acct.email}."
            except Exception:
                continue
        return f"Could not send reply to {msg_id}."

    # ── /mail mark_read <id> ───────────────────────────────────────────
    if low.startswith("/mail mark_read") or low.startswith("/mail mark read"):
        msg_id = text.strip().split()[-1]
        for acct in (cfg.accounts or []):
            try:
                ok = await mark_read(_token_path(cfg, acct.name), creds_file, msg_id, acct.name)
                if ok:
                    return f"Marked as read."
            except Exception:
                continue
        return "Could not mark as read."

    # ── Natural: quick inbox check ─────────────────────────────────────
    if _is_inbox_check(low):
        # Already handled above, but catch NL variants
        return await handle(f"/mail inbox", cfg, user_tz)

    return None


# ── Helpers ────────────────────────────────────────────────────────────────

def _is_inbox_check(low: str) -> bool:
    patterns = [
        r"(any|new|check|what'?s?|show).*(email|inbox|mail)",
        r"(email|inbox|mail).*(new|unread|check)",
    ]
    return any(re.search(p, low) for p in patterns)


def _find_account(cfg, name: str):
    name_low = name.lower()
    for acct in (cfg.accounts or []):
        if acct.name.lower() == name_low or acct.email.lower().startswith(name_low):
            return acct
    return None


def _token_path(cfg, account_name: str) -> str:
    for acct in (cfg.accounts or []):
        if acct.name == account_name:
            return acct.token_file
    return f"/home/hermes/.hermes/gmail_token_{account_name}.json"
