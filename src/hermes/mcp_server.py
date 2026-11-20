"""Hermes MCP Server -- exposes Hermes tools over HTTP/SSE.

Claude Code registers this endpoint and can then call Hermes tools directly
instead of going through the git-push-deploy cycle for every test.

Endpoint (after deploy):
  GET  /mcp/sse         -- SSE stream (server -> client)
  POST /mcp/messages/   -- JSON-RPC messages (client -> server)

Claude Code config (~/.claude.json mcpServers section):
  "hermes": {
    "transport": "sse",
    "url": "http://2.24.197.91:8080/mcp/sse"
  }

Tools exposed:
  query_jarvis(text)              -- ask Hermes a question via LLM router
  get_status()                    -- uptime, model, messages today, spend
  get_messages(limit)             -- recent conversation history from DB
  get_emails(account, limit, unread_only)  -- unread Gmail messages
  get_calendar(hours)             -- upcoming Google Calendar events
  mark_email_read(account, message_id)
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("hermes.mcp")

# Global references set by configure()
_cfg = None
_router = None
_db_path = ""


def configure(cfg, router, db_path: str) -> None:
    global _cfg, _router, _db_path
    _cfg = cfg
    _router = router
    _db_path = db_path


def _check_auth(request_headers: dict) -> bool:
    if _cfg is None:
        return True
    secret = getattr(getattr(_cfg, "dashboard", None), "secret_key", "")
    if not secret or secret in ("change-me", "your-secret-here"):
        return True
    return request_headers.get("x-jarvis-token", "") == secret


def build_mcp_router():
    """Build a FastAPI router with MCP SSE + message endpoints."""
    try:
        from mcp.server import Server
        from mcp.server.sse import SseServerTransport
        from mcp import types
    except ImportError:
        log.warning("mcp package not installed -- MCP server disabled. Run: pip install mcp")
        return None

    from fastapi import APIRouter, Request, HTTPException

    mcp_server = Server("hermes-jarvis")
    sse_transport = SseServerTransport("/mcp/messages/")

    @mcp_server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name="query_jarvis",
                description="Ask Hermes/Jarvis a question. Routes through the LLM (Claude/DeepSeek/Gemini). Returns the text reply.",
                inputSchema={
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "The question or instruction"}},
                    "required": ["text"],
                },
            ),
            types.Tool(
                name="get_status",
                description="Get Hermes system status: uptime, active model, messages today, monthly spend.",
                inputSchema={"type": "object", "properties": {}},
            ),
            types.Tool(
                name="get_messages",
                description="Get recent conversation history from the Hermes database.",
                inputSchema={
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "description": "Number of messages to return (default 20)", "default": 20}},
                },
            ),
            types.Tool(
                name="get_emails",
                description="Get unread Gmail messages across configured accounts.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string", "description": "Account name (paywise/velocity/personal) or 'all'", "default": "all"},
                        "limit": {"type": "integer", "description": "Max messages per account", "default": 5},
                        "unread_only": {"type": "boolean", "default": True},
                    },
                },
            ),
            types.Tool(
                name="get_calendar",
                description="Get upcoming Google Calendar events.",
                inputSchema={
                    "type": "object",
                    "properties": {"hours": {"type": "integer", "description": "Look ahead hours (default 24)", "default": 24}},
                },
            ),
            types.Tool(
                name="mark_email_read",
                description="Mark a Gmail message as read.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "account": {"type": "string", "description": "Account name (paywise/velocity/personal)"},
                        "message_id": {"type": "string", "description": "Gmail message ID"},
                    },
                    "required": ["account", "message_id"],
                },
            ),
        ]

    @mcp_server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        import json

        if name == "query_jarvis":
            text = arguments.get("text", "")
            if not _router:
                return [types.TextContent(type="text", text="Router not available.")]
            response = await _router.route(text, [], max_tokens=200)
            return [types.TextContent(type="text", text=response.text or response.error or "No response.")]

        elif name == "get_status":
            result = await _get_status()
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_messages":
            limit = arguments.get("limit", 20)
            from .db.queries import get_recent_messages
            msgs = await get_recent_messages(_db_path, limit=limit)
            return [types.TextContent(type="text", text=json.dumps(msgs, indent=2, default=str))]

        elif name == "get_emails":
            account = arguments.get("account", "all")
            limit = arguments.get("limit", 5)
            unread_only = arguments.get("unread_only", True)
            result = await _get_emails(account, limit, unread_only)
            return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "get_calendar":
            hours = arguments.get("hours", 24)
            result = await _get_calendar(hours)
            return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "mark_email_read":
            account = arguments.get("account", "")
            message_id = arguments.get("message_id", "")
            result = await _mark_email_read(account, message_id)
            return [types.TextContent(type="text", text=json.dumps(result))]

        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

    router = APIRouter()

    @router.get("/mcp/sse")
    async def mcp_sse_endpoint(request: Request):
        if not _check_auth(dict(request.headers)):
            raise HTTPException(status_code=401, detail="unauthorized")
        async with sse_transport.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await mcp_server.run(
                streams[0], streams[1],
                mcp_server.create_initialization_options(),
            )

    @router.post("/mcp/messages/")
    async def mcp_messages_endpoint(request: Request):
        if not _check_auth(dict(request.headers)):
            raise HTTPException(status_code=401, detail="unauthorized")
        await sse_transport.handle_post_message(
            request.scope, request.receive, request._send
        )

    log.info("MCP server built -- endpoints: GET /mcp/sse  POST /mcp/messages/")
    return router


# ── Tool implementations ───────────────────────────────────────────────────

async def _get_status() -> dict:
    import os, time
    result: dict[str, Any] = {"status": "running"}

    if _db_path:
        try:
            from .db.queries import get_recent_messages, get_monthly_spend
            msgs = await get_recent_messages(_db_path, limit=50)
            spend = await get_monthly_spend(_db_path)
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            today_count = 0
            last_ago = "no messages"
            for m in msgs:
                ts_raw = m.get("created_at", "")
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if ts.date() == now.date():
                        today_count += 1
                    if last_ago == "no messages":
                        diff = int((now - ts).total_seconds())
                        last_ago = f"{diff}s ago" if diff < 60 else f"{diff // 60}m ago" if diff < 3600 else f"{diff // 3600}h ago"
                except Exception:
                    pass
            result["messages_today"] = today_count
            result["last_message_ago"] = last_ago
            result["spend_month_usd"] = round(sum(spend.values()), 4)
            result["spend_by_provider"] = {k: round(v, 4) for k, v in spend.items()}
        except Exception as exc:
            result["db_error"] = str(exc)

    proc_start = os.environ.get("JARVIS_START_TIME")
    if proc_start:
        try:
            import time
            result["uptime_hours"] = round((time.time() - float(proc_start)) / 3600, 1)
        except Exception:
            pass

    if _cfg:
        result["integrations"] = {
            "telegram": bool(_cfg.telegram),
            "voice": bool(_cfg.elevenlabs),
            "calendar": bool(_cfg.calendar),
            "gmail": bool(getattr(_cfg, "gmail", None)),
            "claude": bool(_cfg.anthropic),
            "deepseek": bool(_cfg.deepseek),
            "gemini": bool(_cfg.gemini),
        }

    return result


async def _get_emails(account: str, limit: int, unread_only: bool) -> dict:
    if not _cfg or not getattr(_cfg, "gmail", None):
        return {"error": "Gmail not configured. Add gmail section to config.yaml."}

    gmail_cfg = _cfg.gmail
    from .integrations.gmail import list_messages, format_inbox_text

    results = {}
    accounts = gmail_cfg.accounts or []
    if account != "all":
        accounts = [a for a in accounts if a.name == account or a.email.startswith(account)]

    for acct in accounts:
        token_file = acct.token_file
        try:
            msgs = await list_messages(
                token_file, gmail_cfg.credentials_file,
                account_name=acct.name, unread_only=unread_only, max_results=limit,
            )
            results[acct.name] = [
                {
                    "id": m["id"],
                    "from": m["from"],
                    "subject": m["subject"],
                    "date": str(m["date"]) if m.get("date") else None,
                    "snippet": m["snippet"][:200],
                    "unread": m["unread"],
                }
                for m in msgs
            ]
        except RuntimeError:
            results[acct.name] = {"error": f"Not authenticated. Run /mail auth {acct.name} in Telegram or HUD."}
        except Exception as exc:
            results[acct.name] = {"error": str(exc)}

    return results


async def _get_calendar(hours: int) -> dict:
    if not _cfg or not _cfg.calendar:
        return {"error": "Calendar not configured."}

    cal_cfg = _cfg.calendar
    from .integrations.google_calendar import list_upcoming

    results = {}
    for acct in (cal_cfg.accounts or []):
        try:
            events = await list_upcoming(
                acct.token_file, cal_cfg.credentials_file,
                hours=hours, user_tz=cal_cfg.timezone, account_name=acct.name,
            )
            results[acct.name] = [
                {
                    "id": e["id"],
                    "title": e["title"],
                    "start": str(e["start"]) if e.get("start") else None,
                    "location": e.get("location", ""),
                }
                for e in events
            ]
        except RuntimeError:
            results[acct.name] = {"error": "Not authenticated. Run /cal auth in Telegram."}
        except Exception as exc:
            results[acct.name] = {"error": str(exc)}

    return results


async def _mark_email_read(account: str, message_id: str) -> dict:
    if not _cfg or not getattr(_cfg, "gmail", None):
        return {"error": "Gmail not configured."}

    gmail_cfg = _cfg.gmail
    from .integrations.gmail import mark_read

    acct = next((a for a in gmail_cfg.accounts if a.name == account), None)
    if not acct:
        return {"error": f"Account '{account}' not found."}

    ok = await mark_read(acct.token_file, gmail_cfg.credentials_file, message_id, account)
    return {"ok": ok}
