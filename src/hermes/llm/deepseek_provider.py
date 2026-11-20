from __future__ import annotations
import httpx
from .base import LLMProvider, LLMResponse

# DeepSeek V3 pricing
_INPUT_PRICE = 0.14 / 1_000_000
_OUTPUT_PRICE = 0.28 / 1_000_000

_API_URL = "https://api.deepseek.com/v1/chat/completions"


class DeepSeekProvider(LLMProvider):
    name = "deepseek"

    def __init__(self, api_key: str, model: str):
        self._api_key = api_key
        self._model = model

    async def complete(self, messages, system=None, max_tokens=1024) -> LLMResponse:
        full_messages = messages
        if system:
            full_messages = [{"role": "system", "content": system}] + messages

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    _API_URL,
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
            cost = inp * _INPUT_PRICE + out * _OUTPUT_PRICE

            return LLMResponse(
                text=text,
                provider="deepseek",
                model=self._model,
                input_tokens=inp,
                output_tokens=out,
                cost_usd=cost,
            )
        except Exception as e:
            import traceback, logging
            logging.getLogger("hermes.llm.deepseek").error("DeepSeek call failed:\n%s", traceback.format_exc())
            return LLMResponse(text="", provider="deepseek", model=self._model, error=str(e))
