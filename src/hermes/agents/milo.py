"""Milo -- email agent.

Runs at 08:00 and 16:00 every day.  For each unread email:
  - Classifies it as noise / needs-attention / draft-ready
  - Drafts a reply for draft-ready emails using the LLM
  - Sends a Telegram message per draft with Send / Edit / Skip buttons

Noise emails are silently counted.  A summary line is sent at the end.
"""
from __future__ import annotations
import asyncio
from .base import HermesAgent


_DRAFT_SYSTEM = """You are Milo, a professional email assistant for {name}.

Your job: read an email and write a short, professional reply on {name}'s behalf.

Rules:
- Match {name}'s tone (professional but direct).
- Be brief. 3-5 sentences max.
- Do not add "Best regards" boilerplate unless the original email was formal.
- Do not make up facts, prices, or commitments you don't have context for.
- If you genuinely cannot draft a reply (needs specific info only {name} has), reply with SKIP.
- Output ONLY the reply text. No subject line, no metadata."""


class MiloAgent(HermesAgent):
    name = "Milo"
    role = "email agent"
    schedule = "08:00,16:00"

    async def run(self) -> None:
        if not getattr(self._cfg, "gmail", None):
            self._log.info("Gmail not configured -- skipping")
            return

        self._log.info("Checking inbox")
        gmail_cfg = self._cfg.gmail

        # Sort accounts by priority: high first
        priority_order = {"high": 0, "medium": 1, "low": 2}
        accounts = sorted(gmail_cfg.accounts, key=lambda a: priority_order.get(a.priority, 9))

        noise = 0
        needs_attention = []
        drafts_sent = 0

        for acct in accounts:
            try:
                from ..integrations.gmail import list_messages, get_message
                msgs = await list_messages(
                    acct.token_file, gmail_cfg.credentials_file,
                    account_name=acct.name, unread_only=True, max_results=15,
                )
            except RuntimeError:
                await self.send(f"<b>Milo:</b> {acct.name} inbox not authenticated. Send /mail auth {acct.name} to connect.")
                continue
            except Exception as exc:
                self._log.warning("Failed to fetch %s: %s", acct.name, exc)
                continue

            for msg in msgs:
                classification = self._classify(msg)

                if classification == "noise":
                    noise += 1
                    continue

                if classification == "needs_attention":
                    needs_attention.append((acct, msg))
                    continue

                # draft_ready -- attempt LLM draft
                draft = await self._draft_reply(msg)
                if not draft or draft.strip().upper() == "SKIP":
                    needs_attention.append((acct, msg))
                    continue

                preview = self._format_approval_message(acct.name, msg, draft)

                async def _send_email(payload={"acct": acct, "msg": msg, "draft": draft}):
                    try:
                        from ..integrations.gmail import send_reply
                        ok = await send_reply(
                            payload["acct"].token_file,
                            gmail_cfg.credentials_file,
                            payload["msg"]["id"],
                            payload["draft"],
                            payload["acct"].name,
                        )
                        return "Sent." if ok else "Failed to send."
                    except Exception as e:
                        return f"Error: {e}"

                await self.send_approval(
                    text=preview,
                    action_type="send_email",
                    payload={"account": acct.name, "message_id": msg["id"], "draft": draft},
                    on_approve=_send_email,
                    on_edit=f"Edit the draft reply to: {msg.get('subject','(no subject)')}",
                )
                drafts_sent += 1
                await asyncio.sleep(0.5)  # avoid Telegram rate limits

        # Summary line
        summary_parts = []
        if noise:
            summary_parts.append(f"{noise} noise")
        if needs_attention:
            summary_parts.append(f"{len(needs_attention)} need your attention")
        if drafts_sent:
            summary_parts.append(f"{drafts_sent} draft(s) ready above")

        if summary_parts:
            await self.send(f"<b>Milo:</b> {', '.join(summary_parts)}.")

        # Flag needs-attention emails
        if needs_attention:
            attn_lines = ["<b>Milo -- needs your attention:</b>"]
            for acct, msg in needs_attention[:5]:
                sender = msg.get("from", "Unknown")[:40]
                subject = msg.get("subject", "(no subject)")[:60]
                attn_lines.append(f"  [{acct.name}] <b>{subject}</b>\n  from: {sender}")
            await self.send("\n\n".join(attn_lines))

    def _classify(self, msg: dict) -> str:
        """noise | needs_attention | draft_ready"""
        subject = (msg.get("subject") or "").lower()
        sender = (msg.get("from") or "").lower()
        snippet = (msg.get("snippet") or "").lower()

        noise_patterns = [
            "unsubscribe", "newsletter", "noreply", "no-reply",
            "notification", "alert", "automated", "do not reply",
            "weekly digest", "monthly digest", "verify your email",
            "confirm your", "receipt for", "invoice #", "order confirmation",
        ]
        for p in noise_patterns:
            if p in subject or p in sender or p in snippet:
                return "noise"

        # Questions, requests, greetings -- draft_ready
        draft_signals = [
            "?", "please", "can you", "could you", "would you",
            "let me know", "following up", "checking in", "quick question",
            "i wanted to", "reaching out",
        ]
        text = f"{subject} {snippet}"
        for s in draft_signals:
            if s in text:
                return "draft_ready"

        return "needs_attention"

    async def _draft_reply(self, msg: dict) -> str:
        if not self._cfg.deepseek and not self._cfg.anthropic:
            return ""
        try:
            from ..llm import DeepSeekProvider, ClaudeProvider
            owner_name = "Nikola"  # TODO: pull from config/memory

            system = _DRAFT_SYSTEM.format(name=owner_name)
            email_text = (
                f"From: {msg.get('from','')}\n"
                f"Subject: {msg.get('subject','')}\n\n"
                f"{msg.get('body') or msg.get('snippet','')}"
            )

            # Use DeepSeek (cheap) for drafts
            if self._cfg.deepseek:
                provider = DeepSeekProvider(self._cfg.deepseek.api_key, self._cfg.deepseek.model)
            else:
                provider = ClaudeProvider(self._cfg.anthropic.api_key, self._cfg.anthropic.model)

            resp = await provider.complete(
                [{"role": "user", "content": email_text}],
                system=system,
                max_tokens=200,
            )
            return resp.text or ""
        except Exception as exc:
            self._log.warning("Draft failed: %s", exc)
            return ""

    def _format_approval_message(self, account: str, msg: dict, draft: str) -> str:
        sender = msg.get("from", "Unknown")[:60]
        subject = msg.get("subject", "(no subject)")[:80]
        snippet = msg.get("snippet", "")[:200]
        return (
            f"<b>Milo</b> [{account}] -- draft ready\n\n"
            f"<b>From:</b> {sender}\n"
            f"<b>Subject:</b> {subject}\n"
            f"<b>Preview:</b> {snippet}\n\n"
            f"<b>Draft reply:</b>\n{draft}"
        )
