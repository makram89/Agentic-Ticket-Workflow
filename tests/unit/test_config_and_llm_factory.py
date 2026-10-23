"""Settings and LLM selection: S-56 (and the config half of S-29)."""
import socket

import pytest

DEFAULT_MODEL = "openai/gpt-4o-mini"
BASE_URL = "https://openrouter.ai/api/v1"


@pytest.fixture
def no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


def test_s56_default_mode_is_fake():
    """S-56 (a)"""
    from ticketflow.config import load_settings
    from ticketflow.services.llm.factory import build_llm
    from ticketflow.services.llm.fake import FakeLLM

    settings = load_settings({})
    assert settings.llm_mode == "fake"
    assert settings.task_queue == "tickets"
    assert settings.approval_timeout_seconds == 86400
    assert settings.helpdesk_failure_rate == 0.0
    assert settings.otel_endpoint is None
    assert isinstance(build_llm(settings), FakeLLM)


def test_s56_openrouter_without_key_is_config_error():
    """S-56 (b)"""
    from ticketflow.config import ConfigError, load_settings
    from ticketflow.services.llm.factory import build_llm

    with pytest.raises(ConfigError) as excinfo:
        build_llm(load_settings({"LLM_MODE": "openrouter"}))
    assert "OPEN_ROUTER_API_KEY" in str(excinfo.value)


def test_s56_unknown_llm_mode_is_config_error():
    """S-56 (c)"""
    from ticketflow.config import ConfigError, load_settings
    from ticketflow.services.llm.factory import build_llm

    with pytest.raises(ConfigError) as excinfo:
        build_llm(load_settings({"LLM_MODE": "anthropic"}))
    assert "LLM_MODE" in str(excinfo.value)


def test_s56_openrouter_with_key_builds_adapter_offline(no_network):
    """S-56 (d)"""
    import openai

    from ticketflow.config import load_settings
    from ticketflow.services.llm.factory import build_llm
    from ticketflow.services.llm.openrouter import OpenRouterLLM

    settings = load_settings({"LLM_MODE": "openrouter", "OPEN_ROUTER_API_KEY": "dummy"})
    assert "dummy" not in repr(settings)
    assert settings.openrouter_model == DEFAULT_MODEL

    llm = build_llm(settings)
    assert isinstance(llm, OpenRouterLLM)
    assert OpenRouterLLM.BASE_URL == BASE_URL

    clients = [v for v in vars(llm).values() if isinstance(v, openai.AsyncOpenAI)]
    assert len(clients) == 1, "OpenRouterLLM should hold its openai.AsyncOpenAI client"
    client = clients[0]
    assert str(client.base_url).rstrip("/") == BASE_URL
    assert client.max_retries == 0
    assert DEFAULT_MODEL in [v for v in vars(llm).values() if isinstance(v, str)]


def test_s56_settings_read_from_env_names():
    """S-56 / R18: every setting comes from its environment variable."""
    from ticketflow.config import load_settings

    s = load_settings(
        {
            "TEMPORAL_ADDRESS": "temporal:7233",
            "TEMPORAL_NAMESPACE": "ns",
            "TEMPORAL_TASK_QUEUE": "tq",
            "LLM_MODE": "fake",
            "OPENROUTER_MODEL": "some/model",
            "LLM_MAX_TURNS": "5",
            "LLM_MAX_INVALID_ANSWERS": "2",
            "HELPDESK_FAILURE_RATE": "0.2",
            "APPROVAL_TIMEOUT_SECONDS": "60",
            "LOG_LEVEL": "DEBUG",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://jaeger:4318",
        }
    )
    assert s.temporal_address == "temporal:7233"
    assert s.temporal_namespace == "ns"
    assert s.task_queue == "tq"
    assert s.openrouter_model == "some/model"
    assert s.llm_max_turns == 5
    assert s.llm_max_invalid_answers == 2
    assert s.helpdesk_failure_rate == pytest.approx(0.2)
    assert s.approval_timeout_seconds == 60
    assert s.log_level == "DEBUG"
    assert s.otel_endpoint == "http://jaeger:4318"


@pytest.mark.parametrize("value", ["1.5", "abc", "-0.1"])
def test_s29_invalid_failure_rate_is_config_error(value):
    """S-29 (config part)"""
    from ticketflow.config import ConfigError, load_settings

    with pytest.raises(ConfigError) as excinfo:
        load_settings({"HELPDESK_FAILURE_RATE": value})
    assert "HELPDESK_FAILURE_RATE" in str(excinfo.value)
