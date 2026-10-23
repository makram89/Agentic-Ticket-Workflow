"""Settings from environment variables (spec R18)."""
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

LLM_MODES = ("fake", "openrouter")


class ConfigError(Exception):
    """A setting is missing or invalid; raised at process start."""


@dataclass(frozen=True)
class Settings:
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    task_queue: str = "tickets"
    llm_mode: str = "fake"
    openrouter_api_key: str | None = field(default=None, repr=False)
    openrouter_model: str = "openai/gpt-4o-mini"
    llm_max_turns: int = 8
    llm_max_invalid_answers: int = 3
    helpdesk_failure_rate: float = 0.0
    approval_timeout_seconds: int = 86400
    log_level: str = "INFO"
    otel_endpoint: str | None = None
    otel_service_name: str | None = None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    def get(name: str) -> str | None:
        value = env.get(name)
        return value.strip() if value and value.strip() else None

    def get_int(name: str, default: int) -> int:
        raw = get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
        if value < 1:
            raise ConfigError(f"{name} must be >= 1, got {value}")
        return value

    llm_mode = (get("LLM_MODE") or "fake").lower()
    if llm_mode not in LLM_MODES:
        raise ConfigError(f"LLM_MODE must be one of {', '.join(LLM_MODES)}, got {llm_mode!r}")

    raw_rate = get("HELPDESK_FAILURE_RATE")
    try:
        failure_rate = float(raw_rate) if raw_rate is not None else 0.0
    except ValueError:
        raise ConfigError(f"HELPDESK_FAILURE_RATE must be a number in [0, 1], got {raw_rate!r}") from None
    if not 0.0 <= failure_rate <= 1.0:
        raise ConfigError(f"HELPDESK_FAILURE_RATE must be in [0, 1], got {raw_rate!r}")

    return Settings(
        temporal_address=get("TEMPORAL_ADDRESS") or Settings.temporal_address,
        temporal_namespace=get("TEMPORAL_NAMESPACE") or Settings.temporal_namespace,
        task_queue=get("TEMPORAL_TASK_QUEUE") or Settings.task_queue,
        llm_mode=llm_mode,
        openrouter_api_key=get("OPEN_ROUTER_API_KEY"),
        openrouter_model=get("OPENROUTER_MODEL") or Settings.openrouter_model,
        llm_max_turns=get_int("LLM_MAX_TURNS", Settings.llm_max_turns),
        llm_max_invalid_answers=get_int("LLM_MAX_INVALID_ANSWERS", Settings.llm_max_invalid_answers),
        helpdesk_failure_rate=failure_rate,
        approval_timeout_seconds=get_int("APPROVAL_TIMEOUT_SECONDS", Settings.approval_timeout_seconds),
        log_level=(get("LOG_LEVEL") or Settings.log_level).upper(),
        otel_endpoint=get("OTEL_EXPORTER_OTLP_ENDPOINT"),
        otel_service_name=get("OTEL_SERVICE_NAME"),
    )
