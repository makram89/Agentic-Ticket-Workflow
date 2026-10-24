"""Static checks on the repository: S-55, S-60, S-61, S-62 (workflow determinism), S-66."""
import ast
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

R18_NAMES = [
    "TEMPORAL_ADDRESS",
    "TEMPORAL_NAMESPACE",
    "TEMPORAL_TASK_QUEUE",
    "LLM_MODE",
    "OPEN_ROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "LLM_MAX_TURNS",
    "LLM_MAX_INVALID_ANSWERS",
    "HELPDESK_FAILURE_RATE",
    "APPROVAL_TIMEOUT_SECONDS",
    "LOG_LEVEL",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SERVICE_NAME",
]


def _imports(path: Path):
    """Absolute module names imported by a module (relative imports resolved)."""
    tree = ast.parse(path.read_text(), filename=str(path))
    pkg_parts = list(path.relative_to(ROOT).with_suffix("").parts[:-1])
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg_parts[: len(pkg_parts) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
                if node.module:
                    names.append(mod)
                else:
                    names.extend(f"{mod}.{a.name}" for a in node.names)
            else:
                mod = node.module or ""
                if mod == "ticketflow":  # `from ticketflow import models` -> ticketflow.models
                    names.extend(f"{mod}.{a.name}" for a in node.names)
                else:
                    names.append(mod)
    return names


def test_s55_rules_are_framework_free():
    """S-55"""
    files = sorted((ROOT / "ticketflow" / "rules").rglob("*.py"))
    assert any(f.name == "decision_rules.py" for f in files)
    forbidden_roots = {"fastapi", "starlette", "temporalio", "openai", "opentelemetry", "httpx", "requests",
                       "aiohttp", "urllib3", "pydantic_settings"}
    bad = []
    for f in files:
        for name in _imports(f):
            root = name.split(".")[0]
            if root in forbidden_roots:
                bad.append((f.name, name))
            elif root == "ticketflow":
                allowed = (
                    name == "ticketflow.models"
                    or name.startswith("ticketflow.models.")
                    or name == "ticketflow.rules"
                    or name.startswith("ticketflow.rules.")
                )
                if not allowed:
                    bad.append((f.name, name))
    assert bad == []


def test_s62_workflow_code_has_no_io_randomness_or_clock():
    """S-62 / N11: workflow module does not import I/O, randomness or wall-clock modules."""
    files = sorted((ROOT / "ticketflow" / "workflow").rglob("*.py"))
    assert any(f.name == "ticket_workflow.py" for f in files)
    forbidden_roots = {"random", "time", "socket", "httpx", "requests", "openai", "urllib", "subprocess"}
    for f in files:
        src = f.read_text()
        for name in _imports(f):
            for root in forbidden_roots:
                assert not (name == root or name.startswith(root + ".")), (f.name, name)
        assert not re.search(r"\b(datetime\.now|datetime\.utcnow|date\.today)\s*\(", src), f.name


def _parse_env_example():
    values = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        values[key.strip().removeprefix("export ").strip()] = value
    return values


def test_s60_env_example():
    """S-60"""
    values = _parse_env_example()
    for name in R18_NAMES:
        assert name in values, name
    assert values["LLM_MODE"] == "fake"
    assert values["OPEN_ROUTER_API_KEY"] == ""
    assert values["OPENROUTER_MODEL"] == "openai/gpt-4o-mini"
    assert values["HELPDESK_FAILURE_RATE"] == "0.2"
    assert values["OTEL_EXPORTER_OTLP_ENDPOINT"] == ""


def _service_env(service):
    env = service.get("environment") or {}
    if isinstance(env, list):
        out = {}
        for item in env:
            k, _, v = str(item).partition("=")
            out[k] = v
        return out
    return {k: ("" if v is None else str(v)) for k, v in env.items()}


def test_s60_docker_compose():
    """S-60"""
    text = (ROOT / "docker-compose.yml").read_text()
    compose = yaml.safe_load(text)
    services = compose["services"]
    assert {"temporal", "api", "worker", "jaeger"} <= set(services)
    for name in ("api", "worker"):
        env = _service_env(services[name])
        assert "jaeger" in env.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""), name
        assert "4318" in env.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""), name
        assert env.get("OTEL_SERVICE_NAME"), name
        llm_mode = env.get("LLM_MODE", "fake")
        assert "fake" in llm_mode, name
    # no required key: no ${VAR:?err} / ${VAR?err} for the API key
    assert not re.search(r"\$\{OPEN_ROUTER_API_KEY:?\?", text)


def test_s60_gitignore_and_no_assignment_text():
    """S-60 / N7"""
    lines = {line.strip() for line in (ROOT / ".gitignore").read_text().splitlines()}
    assert ".env" in lines or "/.env" in lines
    assert "taskaiticketworkflow.md" in lines or "/taskaiticketworkflow.md" in lines
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert "taskaiticketworkflow.md" not in tracked
    assert ".env" not in tracked


def test_s60_pyproject_has_no_database_or_vector_store():
    """S-60 / N14"""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = data["project"]
    deps = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        deps.extend(extra)
    names = {re.split(r"[<>=!~\[;\s]", d.strip(), maxsplit=1)[0].lower() for d in deps}
    forbidden = {
        "sqlalchemy", "sqlmodel", "alembic", "psycopg", "psycopg2", "psycopg2-binary", "asyncpg", "pymongo",
        "motor", "peewee", "tortoise-orm", "django", "redis", "chromadb", "faiss-cpu", "faiss-gpu",
        "pinecone", "pinecone-client", "qdrant-client", "weaviate-client", "pgvector", "sentence-transformers",
        "lancedb", "milvus", "pymilvus",
    }
    assert names & forbidden == set()
    assert {"temporalio", "fastapi", "pydantic", "openai"} <= {n.split("[")[0] for n in names}
    assert "dev" in project.get("optional-dependencies", {})


README_HEADINGS = [
    "What it does",
    "Flow",
    "Tech stack",
    "Run",
    "API",
    "Tracing",
    "Tests",
]


def test_s61_readme_headings():
    """S-61 (automated part; the docker compose acceptance run is manual)"""
    text = (ROOT / "README.md").read_text()
    headings = [line.lstrip("#").strip().lower() for line in text.splitlines() if line.startswith("#")]
    for wanted in README_HEADINGS:
        assert any(wanted.lower() in h for h in headings), wanted
    assert "```mermaid" in text
    for endpoint in ("/tickets", "/approval"):
        assert endpoint in text
    assert text.count("curl") >= 3
    assert "16686" in text  # how to view traces in Jaeger


def _workflow_yaml():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "tests.yml").read_text())


def test_s66_ci_workflow():
    """S-66"""
    raw = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
    wf = _workflow_yaml()
    triggers = wf.get("on", wf.get(True))  # PyYAML parses a bare `on` key as True
    if isinstance(triggers, str):
        triggers = [triggers]
    assert "push" in triggers

    steps = [s for job in wf["jobs"].values() for s in job.get("steps", [])]
    py_versions = [str((s.get("with") or {}).get("python-version")) for s in steps if "setup-python" in str(s.get("uses"))]
    assert "3.12" in py_versions
    assert any("pytest -q" in str(s.get("run", "")) for s in steps)

    envs = [wf.get("env") or {}]
    envs += [job.get("env") or {} for job in wf["jobs"].values()]
    envs += [s.get("env") or {} for s in steps]
    for env in envs:
        if "LLM_MODE" in env:
            assert env["LLM_MODE"] == "fake"
    assert "secrets." not in raw


@pytest.mark.parametrize("path", ["Dockerfile", "README.md", ".env.example", "docker-compose.yml", "pyproject.toml"])
def test_s60_required_repo_files_exist(path):
    """S-60 / R19 / R23"""
    assert (ROOT / path).is_file()
