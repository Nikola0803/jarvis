from __future__ import annotations
import logging
import httpx
from .base import LLMProvider, LLMResponse


class OpenAICompatProvider(LLMProvider):
    """Generic OpenAI-compatible provider.

    Works with Groq, Together AI, Fireworks, Ollama, OpenRouter, etc.
    Pass the base_url for the target API, e.g.:
      Groq:        https://api.groq.com/openai/v1/chat/completions
      Together AI: https://api.together.xyz/v1/chat/completions
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        provider_name: str = "openai_compat",
        input_price_per_1m: float = 0.0,
        output_price_per_1m: float = 0.0,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self.name = provider_name
        self._inp_price = input_price_per_1m / 1_000_000
        self._out_price = output_price_per_1m / 1_000_000
        self._log = logging.getLogger(f"hermes.llm.{provider_name}")

    async def complete(self, messages, system=None, max_tokens=1024) -> LLMResponse:
        full_messages = ([{"role": "system", "content": system}] if system else []) + list(messages)

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    self._base_url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": full_messages,
                        "max_tokens": max_tokens,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage", {})
            inp = usage.get("prompt_tokens", 0)
            out = usage.get("completion_tokens", 0)
            cost = inp * self._inp_price + out * self._out_price

            return LLMResponse(
                text=text,
                provider=self.name,
                model=self._model,
                input_tokens=inp,
                output_tokens=out,
                cost_usd=cost,
            )
        except Exception as e:
            import traceback
            self._log.error("%s call failed:\n%s", self.name, traceback.format_exc())
            return LLMResponse(text="", provider=self.name, model=self._model, error=str(e))
