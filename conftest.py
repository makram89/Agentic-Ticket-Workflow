"""Root test configuration.

S-62: tests run offline, without an API key and without an OTLP collector, so the
relevant environment variables are removed before any test (or production module) runs.
"""
import os

import pytest
import pytest_asyncio

for _var in ("OPEN_ROUTER_API_KEY", "LLM_MODE", "OTEL_EXPORTER_OTLP_ENDPOINT"):
    os.environ.pop(_var, None)


@pytest_asyncio.fixture
async def workflow_env():
    """Fresh time-skipping Temporal test environment per test (isolated workflow ids).

    Imports are done here (not at module level) so a missing dependency fails each
    test individually instead of aborting collection.
    """
    from temporalio.contrib.pydantic import pydantic_data_converter
    from temporalio.testing import WorkflowEnvironment

    kwargs = {}
    existing = os.environ.get("TEMPORAL_TEST_SERVER_PATH")
    if existing:
        kwargs["test_server_existing_path"] = existing
    env = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter, **kwargs
    )
    try:
        yield env
    finally:
        await env.shutdown()


@pytest.fixture
def repo():
    """Small in-memory DataRepo from tests/fixtures.py (never the seed files)."""
    from tests import fixtures as fx

    return fx.make_repo()
