"""Long-term memory backend.

This is the cross-session "facts brain" (Day 2 of the challenge). Conversation
history (the last N turns) is handled separately in db/queries.py; this layer
stores durable facts about the user in an external memory service and retrieves
only the relevant ones for each new message, so answers improve while token
usage stays small.

Design goals:
- Every call is best-effort. A memory outage must never break a chat reply, so
  all network work is wrapped and failures are logged and swallowed.
- Endpoints are configurable. Supermemory's canonical surface is
  POST /v3/documents (write) and POST /v4/search (retrieve), but the paths are
  overridable in config so a future API change needs no code change.
- Pluggable. `build_memory()` returns a backend or None; new providers can be
  added behind the same MemoryBackend interface.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)


class MemoryBackend(ABC):
    """Interface every long-term memory provider implements."""

    @abstractmethod
    async def search(self, query: str, user_id: str) -> str:
        """Return a short plaintext block of relevant facts, or '' if none."""

    @abstractmethod
    async def add(self, text: str, user_id: str, metadata: Optional[dict] = None) -> None:
        """Persist an exchange / fact. Best-effort; never raises."""

    async def aclose(self) -> None:  # pragma: no cover - lifecycle hook
        return None


class SupermemoryBackend(MemoryBackend):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.supermemory.ai",
        add_path: str = "/v3/documents",
        search_path: str = "/v4/search",
        search_limit: int = 5,
        timeout: float = 6.0,
        namespace: str = "hermes",
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._add_path = add_path
        self._search_path = search_path
        self._search_limit = search_limit
        self._namespace = namespace
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def _container_tags(self, user_id: str) -> list[str]:
        # Scope memories to this agent + this user so retrieval stays personal.
        return [self._namespace, f"{self._namespace}:{user_id}"]

    async def add(self, text: str, user_id: str, metadata: Optional[dict] = None) -> None:
        if not text.strip():
            return
        payload: dict[str, Any] = {
            "content": text,
            "containerTags": self._container_tags(user_id),
        }
        if metadata:
            payload["metadata"] = metadata
        try:
            resp = await self._client.post(self._add_path, json=payload)
            if resp.status_code >= 400:
                log.warning("supermemory add failed: %s %s", resp.status_code, resp.text[:200])
        except Exception as exc:  # noqa: BLE001 - never break the chat flow
            log.warning("supermemory add error: %s", exc)

    async def search(self, query: str, user_id: str) -> str:
        if not query.strip():
            return ""
        payload = {
            "q": query,
            "containerTags": self._container_tags(user_id),
            "limit": self._search_limit,
        }
        try:
            resp = await self._client.post(self._search_path, json=payload)
            if resp.status_code >= 400:
                log.warning("supermemory search failed: %s %s", resp.status_code, resp.text[:200])
                return ""
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("supermemory search error: %s", exc)
            return ""
        return self._format_results(data)

    @staticmethod
    def _format_results(data: Any) -> str:
        """Pull human-readable snippets out of a search response.

        Response shapes have shifted across API versions, so walk the common
        locations defensively rather than assume one schema.
        """
        results = []
        if isinstance(data, dict):
            results = data.get("results") or data.get("memories") or data.get("documents") or []
        elif isinstance(data, list):
            results = data

        snippets: list[str] = []
        for item in results:
            if isinstance(item, str):
                snippets.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = (
                item.get("memory")
                or item.get("content")
                or item.get("summary")
                or item.get("text")
            )
            if not text:
                chunks = item.get("chunks") or []
                parts = [
                    c.get("content") or c.get("text")
                    for c in chunks
                    if isinstance(c, dict) and (c.get("content") or c.get("text"))
                ]
                text = " ".join(p for p in parts if p)
            if text:
                snippets.append(text.strip())

        # De-duplicate while preserving order, cap total size.
        seen: set[str] = set()
        out: list[str] = []
        for s in snippets:
            if s and s not in seen:
                seen.add(s)
                out.append(f"- {s}")
            if len(out) >= 8:
                break
        return "\n".join(out)

    async def aclose(self) -> None:
        await self._client.aclose()


def build_memory(cfg: Optional["MemoryConfig"]) -> Optional[MemoryBackend]:  # noqa: F821
    """Construct a memory backend from config, or None when disabled.

    Returns None (so callers no-op) when memory is off or no API key is set,
    which keeps the agent fully functional before Supermemory is wired up.
    """
    if cfg is None or not getattr(cfg, "enabled", False):
        return None
    provider = (cfg.provider or "supermemory").lower()
    if provider == "supermemory":
        if not cfg.api_key:
            log.warning("memory.enabled is true but no api_key set; disabling long-term memory")
            return None
        return SupermemoryBackend(
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            add_path=cfg.add_path,
            search_path=cfg.search_path,
            search_limit=cfg.search_limit,
            timeout=cfg.timeout_seconds,
            namespace=cfg.namespace,
        )
    log.warning("unknown memory provider %r; long-term memory disabled", provider)
    return None
