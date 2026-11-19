from __future__ import annotations
import logging
from .config import RouterConfig, Config
from .llm import ClaudeProvider, DeepSeekProvider, GeminiProvider, LLMResponse

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Jarvis, a personal AI assistant. You are helpful, concise and direct.
You are running 24/7 on a private VPS owned by your operator.
Never reveal API keys, system configurations or private information.
Respond in the same language the user uses."""

# Keywords that indicate a message needs real thinking (never route to free tier)
_COMPLEX_KEYWORDS = frozenset([
    "explain", "analyze", "analyse", "compare", "write", "draft", "code",
    "debug", "review", "summarize", "summarise", "translate", "research",
    "plan", "strategy", "help me", "how do", "how to", "why does", "why is",
    "what should", "can you", "please", "create", "build", "make", "design",
    "schedule", "calendar", "meeting", "email", "report", "proposal",
])


class Router:
    def __init__(self, cfg: Config):
        self._cfg = cfg.router
        self._db_path = cfg.database.path
        self._spend_cap = cfg.anthropic.spend_cap_usd

        self._providers: dict[str, any] = {
            "claude": ClaudeProvider(cfg.anthropic.api_key, cfg.anthropic.model),
            "deepseek": DeepSeekProvider(cfg.deepseek.api_key, cfg.deepseek.model),
        }
        if cfg.gemini:
            self._providers["gemini"] = GeminiProvider(cfg.gemini.api_key, cfg.gemini.model)
        else:
            # No Gemini key -- reroute free tier and fallback to deepseek
            self._cfg = RouterConfig(
                **{**self._cfg.model_dump(), "free_fallback": "deepseek"}
            )

    def _select_provider(self, text: str, history: list[dict]) -> str:
        text_lower = text.lower().strip()

        # Explicit trigger prefix -> always Claude
        for prefix in self._cfg.claude_trigger_prefixes:
            if text_lower.startswith(prefix):
                log.debug("Claude trigger prefix matched: %s", prefix)
                return "claude"

        words = text.split()
        word_count = len(words)

        # Ultra-simple messages -> Gemini Flash (free tier)
        # Conditions: very short, no complex keywords, no question that needs reasoning
        if word_count <= self._cfg.free_tier_max_words:
            has_complex = any(kw in text_lower for kw in _COMPLEX_KEYWORDS)
            if not has_complex:
                log.debug("Ultra-simple message (%d words) -> free tier", word_count)
                return self._cfg.free_fallback

        # Short messages -> DeepSeek (cheap)
        if word_count <= self._cfg.short_message_max_tokens // 4:
            return self._cfg.default_provider

        return self._cfg.default_provider

    async def _check_spend_cap(self) -> bool:
        from .db.queries import get_monthly_spend
        spend = await get_monthly_spend(self._db_path)
        total = spend.get("claude", 0.0)
        if total >= self._spend_cap:
            log.warning("Claude spend cap reached: $%.2f >= $%.2f", total, self._spend_cap)
            return False
        return True

    async def route(self, text: str, history: list[dict] | None = None) -> LLMResponse:
        history = history or []
        messages = history + [{"role": "user", "content": text}]

        provider_name = self._select_provider(text, history)

        # Enforce spend cap on Claude
        if provider_name == "claude":
            if not await self._check_spend_cap():
                provider_name = self._cfg.default_provider
                log.info("Falling back to %s (spend cap)", provider_name)

        provider = self._providers[provider_name]
        log.info("Routing to %s", provider_name)
        response = await provider.complete(messages, system=SYSTEM_PROMPT)

        # Fallback if provider errored
        if response.error and provider_name != self._cfg.free_fallback:
            log.warning("%s failed (%s), falling back to %s", provider_name, response.error, self._cfg.free_fallback)
            fallback = self._providers[self._cfg.free_fallback]
            response = await fallback.complete(messages, system=SYSTEM_PROMPT)

        return response
