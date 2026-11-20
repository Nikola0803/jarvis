"""Dawn -- morning briefing agent.

Runs at 08:00 every day.  Pulls together:
  - Unread email counts per account
  - Today's calendar events
  - Any pending approvals from overnight agents

Sends one clean briefing message to Telegram.
"""
from __future__ import annotations
from .base import HermesAgent


class DawnAgent(HermesAgent):
    name = "Dawn"
    role = "morning briefing"
    schedule = "08:00"

    async def run(self) -> None:
        self._log.info("Running morning briefing")
        lines = ["<b>Good morning. Here's your day.</b>\n"]

        # Calendar events today
        cal_section = await self._calendar_today()
        if cal_section:
            lines.append(cal_section)

        # Email inbox summary
        mail_section = await self._email_summary()
        if mail_section:
            lines.append(mail_section)

        # Pending approvals
        pending = [v for v in self._approvals._pending.values()]
        if pending:
            lines.append(f"<b>Pending decisions:</b> {len(pending)} item(s) waiting for your sign-off.")

        if len(lines) == 1:
            lines.append("Nothing urgent. Clean slate.")

        await self.send("\n".join(lines))

    async def _calendar_today(self) -> str:
        if not self._cfg.calendar:
            return ""
        try:
            from ..integrations.google_calendar import list_upcoming
            import zoneinfo
            from datetime import datetime

            tz = self._cfg.calendar.timezone
            cal_cfg = self._cfg.calendar
            events = []
            for acct in cal_cfg.accounts:
                try:
                    acct_events = await list_upcoming(
                        acct.token_file, cal_cfg.credentials_file,
                        hours=16, user_tz=tz, account_name=acct.name,
                    )
                    events.extend(acct_events)
                except Exception:
                    pass

            if not events:
                return "<b>Calendar:</b> Nothing scheduled today."

            # Filter to today only
            try:
                now = datetime.now(zoneinfo.ZoneInfo(tz))
                today_events = [e for e in events if e.get("start") and
                                hasattr(e["start"], "date") and
                                e["start"].date() == now.date()]
            except Exception:
                today_events = events[:5]

            if not today_events:
                return "<b>Calendar:</b> Nothing scheduled today."

            lines = [f"<b>Calendar ({len(today_events)} event(s)):</b>"]
            for e in today_events[:5]:
                start = e.get("start")
                time_str = start.strftime("%H:%M") if hasattr(start, "strftime") else str(start)
                lines.append(f"  {time_str} - {e.get('title', 'Untitled')}")
            return "\n".join(lines)
        except Exception as exc:
            self._log.warning("Calendar fetch failed: %s", exc)
            return ""

    async def _email_summary(self) -> str:
        if not getattr(self._cfg, "gmail", None):
            return ""
        try:
            from ..integrations.gmail import list_messages
            gmail_cfg = self._cfg.gmail
            total = 0
            high_priority = 0
            lines = ["<b>Inbox:</b>"]

            for acct in gmail_cfg.accounts:
                try:
                    msgs = await list_messages(
                        acct.token_file, gmail_cfg.credentials_file,
                        account_name=acct.name, unread_only=True, max_results=20,
                    )
                    count = len(msgs)
                    total += count
                    if acct.priority == "high":
                        high_priority += count
                    if count > 0:
                        lines.append(f"  {acct.name}: {count} unread")
                except RuntimeError:
                    lines.append(f"  {acct.name}: not authenticated")
                except Exception:
                    pass

            if total == 0:
                return "<b>Inbox:</b> All clear."
            if high_priority > 0:
                lines.insert(1, f"  {high_priority} high-priority need attention.")
            return "\n".join(lines)
        except Exception as exc:
            self._log.warning("Email summary failed: %s", exc)
            return ""
