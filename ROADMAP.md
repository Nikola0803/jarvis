# Hermes Agent + Z.E.R.O HUD -- Roadmap

> Hermes = the brain (all processing, integrations, data)
> Z.E.R.O HUD = the face (voice I/O, visual feedback, quick panels)

Last updated: 2026-11-20

---

## Architecture Vision

```
Claude Code (this session)
     |  MCP over HTTP/SSE
     v
[Hermes FastAPI :8080]  <--- Z.E.R.O HUD (Next.js :3010, voice/text I/O)
     |
     +-- LLM Router (Claude / DeepSeek / Gemini)
     +-- Voice (ElevenLabs STT + TTS)
     +-- Gmail (3 accounts -- paywise, velocity, personal)
     +-- Google Calendar (velocity72h primary)
     +-- Telegram Bot (personal comms)
     +-- SQLite DB (message history, spend tracking)
     +-- Self-Improve (Anthropic API, /update command)
```

---

## Status Legend

- [x] Done and deployed
- [~] Code written, needs deploy/test
- [ ] Not started

---

## Phase 1: Voice + HUD Polish (done this session)

- [x] Streaming TTS -- ElevenLabs turbo, starts playing in ~1s
- [x] Password gate on HUD (/login page, Next.js middleware, HTTP-only cookie)
- [x] Emergency text input panel on HUD (keyboard icon, /api/chat endpoint)
- [x] Shorter LLM responses (system prompt + max_tokens caps per channel)
- [x] Telegram max_tokens capped (300 text, 80 voice)

---

## Phase 2: MCP Server -- Connect Claude Code to Hermes (THIS SPRINT)

**Goal:** I (Claude Code) talk directly to Hermes instead of editing files blindly.

- [~] `src/hermes/mcp_server.py` -- MCP server with SSE transport
  - Tools: query_jarvis, get_status, get_messages, get_emails, get_calendar
  - Auth: X-Jarvis-Token header
  - Mounted at /mcp/sse + /mcp/messages/ on FastAPI
- [~] `dashboard/app.py` updated to mount MCP router
- [ ] User registers endpoint in Claude Code MCP config (after deploy)

**Registration command (run after deploy):**
```bash
# Add to ~/.claude.json mcpServers section:
# "hermes": { "transport": "sse", "url": "http://2.24.197.91:8080/mcp/sse" }
```

---

## Phase 3: Gmail Integration (THIS SPRINT)

**Accounts:**
1. nikola@pay-wise.io (Workspace) -- HIGH PRIORITY, reply fast
2. velocity72h@gmail.com -- business, calendar home
3. nikolazivkovic0803@gmail.com -- personal, lots of spam

**Files:**
- [~] `src/hermes/integrations/gmail.py` -- Gmail API (list, read, reply, send, mark_read)
- [~] `src/hermes/integrations/gmail_commands.py` -- command handler (/mail, natural language)
- [~] `src/hermes/config.py` -- GmailConfig added
- [~] `config.example.yaml` -- gmail section added

**Gmail commands (via Telegram or HUD text input):**
```
/mail inbox [account]        -- unread emails, default all accounts
/mail read <id>              -- read full email
/mail reply <id> <text>      -- quick reply
/mail mark_read <id>         -- mark as read
/mail auth [account]         -- start OAuth2 flow
/mail auth_code <code>       -- complete OAuth2
```

**Natural language (auto-detected):**
- "any new emails from paywise?"
- "what's in my inbox?"
- "reply to John say I'll be there at 3"

**Setup needed on VPS:**
1. Same Google credentials file used by Calendar (/etc/hermes/google_credentials.json)
2. Needs gmail.readonly + gmail.modify + gmail.send scopes
3. OAuth2 flow via /mail auth (same OOB pattern as calendar)

---

## Phase 4: Telegram Webhook Fix (THIS SPRINT)

**Problem:** python-telegram-bot polling causes 409 Conflict ~60s after restart.
**Fix:** Switch to webhook mode (FastAPI already running, no extra port needed).

- [~] `main.py` -- replace `start_polling` with `start_webhook` + register webhook URL
- [~] Add `/webhook/telegram` route to FastAPI (auto-handled by PTB webhook)
- Webhook URL: `http://2.24.197.91:8080/webhook/telegram`
  (Note: Telegram requires HTTPS for webhooks -- need nginx + certbot OR use polling with exponential backoff + single-process guard)

**Alternative (simpler, no HTTPS needed):** Add a file lock / PID file so only one process polls at a time.

---

## Phase 5: HUD Email Panel (NEXT)

Z.E.R.O HUD should show:
- Badge count for unread across 3 accounts
- Collapsible inbox panel (last 10 unread, priority sorted)
- Tap to read full email
- Quick reply via text input
- Calendar today widget (next 3 events)

New HUD routes:
- GET /api/jarvis/mail?account=all&unread=true
- GET /api/jarvis/calendar?hours=24
- POST /api/jarvis/mail/reply

---

## Phase 6: Fixes Needed (CRITICAL, do in parallel)

- [ ] Rotate ALL exposed API keys:
  - Telegram bot token (exposed twice in chat -- MUST rotate via @BotFather)
  - DeepSeek API key
  - ElevenLabs API key
  - Gemini API key
- [ ] Fix corrupted Anthropic key in /etc/hermes/config.yaml (bullet points U+2022)
  - Self-improvement /update command is broken until this is fixed
  - Fix script: `python3 -c "import yaml; ...paste key interactively..."`
- [ ] Set real dashboard.secret_key in config.yaml
- [ ] Set real JARVIS_HUD_PASSWORD in frontend/.env.local

---

## Configuration Reference

### /etc/hermes/config.yaml sections

```yaml
telegram:
  bot_token: ...
  owner_user_id: ...

anthropic:
  api_key: ...          # BROKEN -- bullet points, needs manual fix
  model: claude-sonnet-4-5
  spend_cap_usd: 20.0

deepseek:
  api_key: ...          # rotate

gemini:
  api_key: ...          # rotate

elevenlabs:
  api_key: ...          # rotate
  voice_id: 0hXgg9WLFD7H2MgyMq8j   # DO NOT CHANGE -- custom voice

calendar:
  credentials_file: /etc/hermes/google_credentials.json
  accounts:
    - name: velocity
      token_file: /home/hermes/.hermes/google_token_velocity.json
  timezone: Europe/Belgrade

gmail:                  # NEW -- add this section
  credentials_file: /etc/hermes/google_credentials.json
  accounts:
    - name: paywise
      email: nikola@pay-wise.io
      token_file: /home/hermes/.hermes/gmail_token_paywise.json
      priority: high
    - name: velocity
      email: velocity72h@gmail.com
      token_file: /home/hermes/.hermes/gmail_token_velocity.json
      priority: medium
    - name: personal
      email: nikolazivkovic0803@gmail.com
      token_file: /home/hermes/.hermes/gmail_token_personal.json
      priority: low

dashboard:
  host: 127.0.0.1
  port: 8080
  secret_key: ...       # set a real secret
```

### frontend/.env.local (on VPS at /opt/jarvis/frontend/.env.local)

```
JARVIS_TOKEN=<same as dashboard.secret_key>
JARVIS_HUD_PASSWORD=<strong password for HUD login>
```

---

## MCP Tools (after Phase 2 deploy)

Once registered, I can call these directly in Claude Code:

| Tool | Description |
|------|-------------|
| `query_jarvis(text)` | Ask Hermes a question via the LLM router |
| `get_status()` | Uptime, model, messages today, spend this month |
| `get_messages(limit)` | Recent conversation history from DB |
| `get_emails(account, limit, unread_only)` | Unread emails from Gmail |
| `get_calendar(hours)` | Upcoming events from Google Calendar |
| `mark_email_read(account, message_id)` | Mark email as read |

---

## Service Notes

- `jarvis.service` -- Hermes Python daemon (Telegram + FastAPI :8080)
- `zero-hud.service` -- Next.js frontend (:3010)
- Logs: `journalctl -u jarvis -f` / `journalctl -u zero-hud -f`
- Config: /etc/hermes/config.yaml
- DB: /home/hermes/.hermes/hermes.db
- Venv: /opt/jarvis/venv
- Code: /opt/jarvis (deployed from git)
