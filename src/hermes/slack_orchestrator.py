"""Autonomous handoff between Slack agent identities on one shared Hermes brain.

Each agent persona may end a reply with "HANDOFF: <AgentName>" (or "HANDOFF:
none"). After an agent answers, we post its reply under ITS Slack identity, then
route the task to the named agent -- same channel, same Hermes session, so the
whole team shares context -- and repeat, up to MAX_HOPS. This is what lets the
crew collaborate in a channel without a human relaying each step.

Loop safety: a bounded hop count, and we never revisit an agent already in the
chain. Handoff targets are matched against configured agents only; an unknown or
"none" target ends the chain.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("hermes.slack_orch")

MAX_HOPS = 4
_HANDOFF_RE = re.compile(r"HANDOFF:\s*([A-Za-z0-9_\- ]+)", re.IGNORECASE)


class SlackOrchestrator:
    """Runs agent personas via Hermes and posts each as its own Slack identity."""

    def __init__(self, cfg):
        self._cfg = cfg
        from .hermes_client import TEXT_SYSTEM
        # name(lower) -> (display_name, bot_token, system_prompt)
        self._agents: dict[str, tuple[str, str, str]] = {}
        if getattr(cfg, "slack", None) and cfg.slack.bot_token:
            self._agents["zero"] = ("ZERO", cfg.slack.bot_token, TEXT_SYSTEM)
        for a in (getattr(cfg, "slack_agents", None) or []):
            self._agents[a.name.lower()] = (a.name, a.bot_token, a.system or TEXT_SYSTEM)

    def known(self, name: str) -> bool:
        return name.lower() in self._agents

    def _client(self, bot_token: str):
        from slack_sdk.web.async_client import AsyncWebClient
        return AsyncWebClient(token=bot_token)

    async def _run_agent(self, name_lower: str, text: str, channel: str | None) -> str:
        from .hermes_client import ask_hermes
        ha = self._cfg.hermes_agent
        _disp, _token, system = self._agents[name_lower]
        sess = f"slack:{channel}" if channel else "slack:dm"
        reply, err = await ask_hermes(
            ha.base_url, ha.api_key, text,
            model=ha.model, timeout=ha.timeout_seconds,
            session_id=sess, reasoning=False, system=system,
        )
        if err:
            log.warning("agent %s error: %s", self._agents[name_lower][0], err)
        return (reply or "").strip() or "(no response)"

    async def _post_as(self, name_lower: str, channel: str, text: str) -> None:
        disp, token, _system = self._agents[name_lower]
        try:
            await self._client(token).chat_postMessage(channel=channel, text=text)
        except Exception as exc:  # noqa: BLE001
            log.warning("post as %s failed: %s", disp, exc)

    def _parse_handoff(self, reply: str) -> str | None:
        m = _HANDOFF_RE.search(reply or "")
        if not m:
            return None
        target = m.group(1).strip().lower()
        if target in ("none", "end", "stop", ""):
            return None
        if target in self._agents:
            return target
        for key in self._agents:  # tolerate partial names ("dev" vs "developer")
            if key.startswith(target) or target.startswith(key):
                return key
        return None

    async def run_chain(self, entry_agent: str, text: str, channel: str | None, say=None) -> None:
        """Run the addressed agent, post as it, then follow the HANDOFF chain.

        Hop 0 posts via `say` (the app that received the event, so Slack shows the
        right author); later hops post via each target agent's own bot token, so
        those agents must be members of the channel.
        """
        current = entry_agent.lower()
        if current not in self._agents:
            current = "zero" if "zero" in self._agents else next(iter(self._agents), None)
        if current is None:
            return
        payload = text
        seen: list[str] = []
        for hop in range(MAX_HOPS):
            reply = await self._run_agent(current, payload, channel)
            if hop == 0 and say is not None:
                await say(reply)
            elif channel:
                await self._post_as(current, channel, reply)
            else:
                # DM with no channel to post into as another identity: stop here.
                if say is not None and hop > 0:
                    await say(f"[{self._agents[current][0]}] {reply}")
            seen.append(current)
            nxt = self._parse_handoff(reply)
            if not nxt or nxt == current or nxt in seen:
                break
            prev_disp = self._agents[current][0]
            payload = (f"Handoff from {prev_disp}:\n\n{reply}\n\n"
                       "Continue the task per your own role. End with HANDOFF: <agent or none>.")
            current = nxt
