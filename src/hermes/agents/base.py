"""HermesAgent base class + AgentScheduler.

Each specialist agent (Dawn, Milo, Remy, Scout...) subclasses HermesAgent,
sets its name/schedule, and implements run().  The AgentScheduler runs all
registered agents at their scheduled times in a background asyncio task.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("hermes.agents")


class HermesAgent:
    """Base class for all Hermes specialist agents."""

    name: str = "agent"
    role: str = "generic agent"

    # Schedule: "HH:MM" for daily at that time (e.g. "08:00"),
    #           "HH:MM,HH:MM" for multiple times per day,
    #           None to disable automatic scheduling (manual trigger only).
    schedule: str | None = None

    def __init__(self, cfg, bot, approval_store, db_path: str):
        self._cfg = cfg
        self._bot = bot               # python-telegram-bot Application
        self._approvals = approval_store
        self._db_path = db_path
        self._owner_id: int = cfg.telegram.owner_user_id
        self._log = logging.getLogger(f"hermes.agents.{self.name}")

    async def run(self) -> None:
        """Override in subclass.  Called by scheduler at the scheduled time."""
        raise NotImplementedError

    # ── Telegram helpers ──────────────────────────────────────────────────────

    async def send(self, text: str, parse_mode: str = "HTML") -> None:
        """Send a plain message to the owner."""
        try:
            await self._bot.bot.send_message(
                chat_id=self._owner_id,
                text=text,
                parse_mode=parse_mode,
            )
        except Exception as exc:
            self._log.error("send failed: %s", exc)

    async def send_approval(
        self,
        text: str,
        action_type: str,
        payload: dict[str, Any],
        on_approve=None,
        on_edit: str = "",
        parse_mode: str = "HTML",
    ) -> None:
        """Send a message with Approve / Edit / Skip inline buttons."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        approval_id = self._approvals.register(
            agent_name=self.name,
            description=text[:200],
            action_type=action_type,
            payload=payload,
            on_approve=on_approve,
            on_edit=on_edit,
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Send ✓",  callback_data=f"approve:{approval_id}"),
                InlineKeyboardButton("Edit ✍",  callback_data=f"edit:{approval_id}"),
                InlineKeyboardButton("Skip ✗",  callback_data=f"skip:{approval_id}"),
            ]
        ])

        try:
            await self._bot.bot.send_message(
                chat_id=self._owner_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=keyboard,
            )
        except Exception as exc:
            self._log.error("send_approval failed: %s", exc)


class AgentScheduler:
    """Runs registered HermesAgents at their scheduled times."""

    def __init__(self, user_tz: str = "Europe/Belgrade"):
        self._agents: list[HermesAgent] = []
        self._tz_name = user_tz
        self._task: asyncio.Task | None = None

    def register(self, agent: HermesAgent) -> None:
        self._agents.append(agent)
        log.info("Agent registered: %s (schedule=%s)", agent.name, agent.schedule)

    def _schedule_times(self, agent: HermesAgent) -> list[tuple[int, int]]:
        """Parse schedule string into list of (hour, minute) tuples."""
        if not agent.schedule:
            return []
        result = []
        for part in agent.schedule.split(","):
            part = part.strip()
            try:
                h, m = part.split(":")
                result.append((int(h), int(m)))
            except Exception:
                log.warning("Agent %s has invalid schedule part: %r", agent.name, part)
        return result

    def _now_local(self) -> datetime:
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(self._tz_name)
            return datetime.now(tz)
        except Exception:
            return datetime.now(timezone.utc)

    async def _run_loop(self) -> None:
        log.info("AgentScheduler started (%d agents)", len(self._agents))
        fired_today: set[tuple[str, int, int]] = set()  # (agent_name, h, m)
        last_date = None

        while True:
            await asyncio.sleep(30)   # check every 30 seconds
            now = self._now_local()

            # Reset fired_today at midnight
            if last_date != now.date():
                fired_today.clear()
                last_date = now.date()

            for agent in self._agents:
                for (h, m) in self._schedule_times(agent):
                    key = (agent.name, h, m)
                    if key in fired_today:
                        continue
                    # Fire if we're within the target minute (±30s handled by sleep interval)
                    if now.hour == h and now.minute == m:
                        fired_today.add(key)
                        log.info("Firing agent: %s at %02d:%02d", agent.name, h, m)
                        asyncio.create_task(self._fire(agent))

    async def _fire(self, agent: HermesAgent) -> None:
        try:
            await agent.run()
        except Exception as exc:
            log.exception("Agent %s crashed: %s", agent.name, exc)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_loop())

    async def trigger(self, agent_name: str) -> bool:
        """Manually trigger an agent by name. Returns True if found."""
        for agent in self._agents:
            if agent.name.lower() == agent_name.lower():
                asyncio.create_task(self._fire(agent))
                return True
        return False
