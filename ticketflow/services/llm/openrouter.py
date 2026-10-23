"""Real LLM: OpenRouter through the OpenAI-compatible chat-completions API (D15)."""
from typing import Any

import openai
from temporalio.exceptions import ApplicationError

from ticketflow.services.llm.base import LLMReply, make_reply

DEFAULT_MODEL = "openai/gpt-4o-mini"

# Retrying cannot fix these (bad key, no permission, unknown model, malformed request).
_REJECTED = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
    openai.BadRequestError,
)


class OpenRouterLLM:
    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, client: Any | None = None):
        # SDK retries are off: Temporal's activity retry policy owns retries.
        self._client = client or openai.AsyncOpenAI(base_url=self.BASE_URL, api_key=api_key, max_retries=0, timeout=60)
        self._model = model

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMReply:
        kwargs: dict = {"model": self._model, "messages": messages, "temperature": 0, "max_tokens": 1024}
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        try:
            completion = await self._client.chat.completions.create(**kwargs)
        except _REJECTED as exc:
            # Connection errors, timeouts, 429 and 5xx are not caught: they propagate as retryable.
            # The provider's message is not included, so nothing request-related can leak into history.
            raise ApplicationError(
                f"LLM request rejected by OpenRouter: HTTP {exc.status_code} {type(exc).__name__}",
                type="LLMRequestRejected",
                non_retryable=True,
            ) from None
        message = completion.choices[0].message
        calls = [
            (tc.id, tc.function.name, tc.function.arguments or "")
            for tc in (message.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        return make_reply(message.content, calls)
