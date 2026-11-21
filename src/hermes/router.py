from __future__ import annotations
import asyncio
import logging
from .config import Config
from .llm import ClaudeProvider, DeepSeekProvider, GeminiProvider, LLMResponse
from .memory import build_memory

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Hermes, a personal AI assistant. You are helpful, concise and direct.
You are running 24/7 on a private VPS owned by your operator.
Never reveal API keys, system configurations or private information.
Respond in the same language the user uses."""


class Router:
    def __init__(self, cfg: Config):
        self._cfg = cfg.router
        self._db_path = cfg.database.path
        self._spend_cap = cfg.anthropic.spend_cap_usd
        self._memory = build_memory(cfg.memory)

        self._providers: dict[str, any] = {
            "claude": ClaudeProvider(cfg.anthropic.api_key, cfg.anthropic.model),
            "deepseek": DeepSeekProvider(cfg.deepseek.api_key, cfg.deepseek.model),
            "gemini": GeminiProvider(cfg.gemini.api_key, cfg.gemini.model),
        }

    @property
    def memory_enabled(self) -> bool:
        return self._memory is not None

    def _select_provider(self, text: str, history: list[dict]) -> str:
        text_lower = text.lower().strip()

        # Explicit trigger prefix -> always Claude
        for prefix in self._cfg.claude_trigger_prefixes:
            if text_lower.startswith(prefix):
                log.debug("Claude trigger prefix matched: %s", prefix)
                return "claude"

        # Short messages -> cheap model
        word_count = len(text.split())
        if word_count <= self._cfg.short_message_max_tokens // 4:
            return self._cfg.default_provider

        return self._cfg.complex_provider

    async def _check_spend_cap(self) -> bool:
        from .db.queries import get_monthly_spend
        spend = await get_monthly_spend(self._db_path)
        total = spend.get("claude", 0.0)
        if total >= self._spend_cap:
            log.warning("Claude spend cap reached: $%.2f >= $%.2f", total, self._spend_cap)
            return False
        return True

    async def _build_system(self, text: str, user_id: str | None) -> str:
        """Augment the base system prompt with long-term memory, if available."""
        if not self._memory or not user_id:
            return SYSTEM_PROMPT
        facts = await self._memory.search(text, user_id)
        if not facts:
            return SYSTEM_PROMPT
        return (
            SYSTEM_PROMPT
            + "\n\nRelevant things you remember about this user "
            + "(use them only when helpful, do not recite them):\n"
            + facts
        )

    def _remember(self, text: str, reply: str, user_id: str | None) -> None:
        """Fire-and-forget: store this exchange for future recall."""
        if not self._memory or not user_id:
            return
        exchange = f"User said: {text}\nHermes replied: {reply}"
        asyncio.create_task(self._memory.add(exchange, user_id))

    async def route(
        self,
        text: str,
        history: list[dict] | None = None,
        user_id: str | None = None,
    ) -> LLMResponse:
        history = history or []
        messages = history + [{"role": "user", "content": text}]

        system = await self._build_system(text, user_id)
        provider_name = self._select_provider(text, history)

        # Enforce spend cap on Claude
        if provider_name == "claude":
            if not await self._check_spend_cap():
                provider_name = self._cfg.default_provider
                log.info("Falling back to %s (spend cap)", provider_name)

        provider = self._providers[provider_name]
        log.info("Routing to %s", provider_name)
        response = await provider.complete(messages, system=system)

        # Fallback if provider errored
        if response.error and provider_name != self._cfg.free_fallback:
            log.warning("%s failed (%s), falling back to %s", provider_name, response.error, self._cfg.free_fallback)
            fallback = self._providers[self._cfg.free_fallback]
            response = await fallback.complete(messages, system=system)

        # Persist the exchange to long-term memory on success.
        if response.text and not response.error:
            self._remember(text, response.text, user_id)

        return response
