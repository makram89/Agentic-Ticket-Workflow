"""Select the LLM provider from settings (LLM_MODE)."""
from ticketflow.config import ConfigError, Settings
from ticketflow.services.llm.base import LLMProvider
from ticketflow.services.llm.fake import FakeLLM


def build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_mode == "fake":
        return FakeLLM()
    if settings.llm_mode == "openrouter":
        if not settings.openrouter_api_key:
            raise ConfigError("LLM_MODE=openrouter requires OPEN_ROUTER_API_KEY")
        from ticketflow.services.llm.openrouter import OpenRouterLLM

        return OpenRouterLLM(api_key=settings.openrouter_api_key, model=settings.openrouter_model)
    raise ConfigError(f"unknown LLM_MODE {settings.llm_mode!r}")
