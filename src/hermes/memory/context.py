"""Build the context prefix injected into every LLM system prompt.

The prefix tells the LLM who the owner is, their preferences, and
any relevant facts retrieved for the current query.
"""
from __future__ import annotations
from .store import MemoryStore

_BASE_PROFILE = """You are Hermes, the AI brain of Jarvis -- a personal AI assistant for {name}.

Owner profile:
{profile_lines}

Relevant context:
{facts}

Rules:
- Address the owner directly, never mention "the user".
- Use their preferred tone: direct, no fluff, no em dashes.
- If you learn something new and factual about the owner, their business, or their preferences, note it.
- Never make up facts about the owner's calendar, emails, or business.
"""

_FALLBACK_PROFILE = """You are Hermes, the AI brain of Jarvis -- a personal AI assistant.

Rules:
- Be direct, no fluff, no em dashes.
- If the owner tells you something factual about themselves, their business, or preferences, remember it.
"""


def build_context_prefix(store: MemoryStore, query: str = "") -> str:
    profile = store.all_profile()

    if not profile:
        return _FALLBACK_PROFILE.strip()

    name = profile.get("name", "the owner")

    profile_lines = []
    for k, v in profile.items():
        if k == "name":
            continue
        profile_lines.append(f"- {k}: {v}")

    # Retrieve relevant facts for this specific query
    facts_for_query: list[str] = []
    if query:
        facts = store.search_facts(query, limit=8)
        for f in facts:
            facts_for_query.append(f"- {f['subject']}: {f['relation']} {f['value']}")

    if not facts_for_query:
        # Fall back to most recent facts
        recent = store.recent_facts(limit=5)
        for f in recent:
            facts_for_query.append(f"- {f['subject']}: {f['relation']} {f['value']}")

    return _BASE_PROFILE.format(
        name=name,
        profile_lines="\n".join(profile_lines) if profile_lines else "  (not yet set)",
        facts="\n".join(facts_for_query) if facts_for_query else "  (none yet)",
    ).strip()
