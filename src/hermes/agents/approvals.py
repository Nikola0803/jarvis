"""Pending approval store -- keeps actions waiting for human sign-off.

When an agent wants to do something consequential (send an email, book a
meeting, move money) it registers the action here and sends a Telegram
message with Approve / Edit / Skip buttons.  The callback_query handler
in telegram_bot.py resolves it.
"""
from __future__ import annotations
import uuid
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

log = logging.getLogger("hermes.approvals")


@dataclass
class PendingApproval:
    id: str
    agent_name: str
    description: str          # shown to user in the approval message
    action_type: str          # e.g. "send_email", "book_event", "generic"
    payload: dict[str, Any]   # all data needed to execute the action
    on_approve: Callable[..., Awaitable[str]] | None = None  # async fn -> result str
    on_edit: str = ""         # hint shown when user taps Edit


class ApprovalStore:
    """In-memory store of pending approvals.  One instance shared across agents."""

    def __init__(self):
        self._pending: dict[str, PendingApproval] = {}

    def register(
        self,
        agent_name: str,
        description: str,
        action_type: str,
        payload: dict[str, Any],
        on_approve: Callable[..., Awaitable[str]] | None = None,
        on_edit: str = "",
    ) -> str:
        """Register a pending approval and return its ID (goes in callback_data)."""
        approval_id = uuid.uuid4().hex[:12]
        self._pending[approval_id] = PendingApproval(
            id=approval_id,
            agent_name=agent_name,
            description=description,
            action_type=action_type,
            payload=payload,
            on_approve=on_approve,
            on_edit=on_edit,
        )
        log.debug("Approval registered: %s (%s)", approval_id, action_type)
        return approval_id

    def get(self, approval_id: str) -> PendingApproval | None:
        return self._pending.get(approval_id)

    def remove(self, approval_id: str) -> None:
        self._pending.pop(approval_id, None)

    def clear_agent(self, agent_name: str) -> None:
        stale = [k for k, v in self._pending.items() if v.agent_name == agent_name]
        for k in stale:
            del self._pending[k]


# Global singleton -- imported by agents and telegram_bot
_store: ApprovalStore | None = None


def get_store() -> ApprovalStore:
    global _store
    if _store is None:
        _store = ApprovalStore()
    return _store
