"""Extract facts and profile updates from conversation turns.

Called after each conversation turn. Uses a small LLM prompt to identify
new facts worth storing. Fast and cheap -- uses DeepSeek or Gemini.
"""
from __future__ import annotations
import json
import logging
import re

log = logging.getLogger("hermes.memory.extractor")

_EXTRACT_SYSTEM = """You extract structured facts from a conversation snippet.

Output ONLY valid JSON in this format:
{
  "facts": [
    {"subject": "Ivan", "relation": "is", "value": "main client, reply fast"},
    {"subject": "owner", "relation": "prefers", "value": "direct tone, no fluff"}
  ],
  "profile_updates": {
    "timezone": "Europe/Belgrade"
  }
}

Rules:
- Extract only NEW, specific, factual information about real people, businesses, or preferences.
- Skip generic chat, greetings, questions with no factual answer, and things already obvious.
- subject should be a name, "owner", or a business name -- never a pronoun.
- If nothing worth storing, return {"facts": [], "profile_updates": {}}.
- Output ONLY the JSON object. No markdown, no explanation."""


async def extract_facts(
    user_text: str,
    assistant_reply: str,
    provider,
) -> dict:
    """
    Returns {"facts": [...], "profile_updates": {...}}.
    Never raises -- returns empty on any error.
    """
    snippet = f"User: {user_text[:400]}\nAssistant: {assistant_reply[:400]}"
    try:
        resp = await provider.complete(
            [{"role": "user", "content": snippet}],
            system=_EXTRACT_SYSTEM,
            max_tokens=300,
        )
        text = (resp.text or "").strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        data = json.loads(text)
        return {
            "facts": data.get("facts", []),
            "profile_updates": data.get("profile_updates", {}),
        }
    except Exception as exc:
        log.debug("Fact extraction skipped: %s", exc)
        return {"facts": [], "profile_updates": {}}
