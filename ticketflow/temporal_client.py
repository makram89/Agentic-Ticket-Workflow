"""Temporal client factory shared by the API and the worker."""
import asyncio
import logging

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from ticketflow.config import Settings
from ticketflow.observability.tracing import tracing_interceptor

log = logging.getLogger(__name__)


async def connect(settings: Settings, *, attempts: int = 10) -> Client:
    """Connect with bounded backoff (Temporal may still be starting under docker compose)."""
    for attempt in range(1, attempts + 1):
        try:
            return await Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
                data_converter=pydantic_data_converter,
                interceptors=[tracing_interceptor()],  # also applied to workers built from this client
            )
        except Exception as exc:
            if attempt == attempts:
                raise
            delay = min(2 ** (attempt - 1), 10)
            log.warning("temporal not reachable at %s (%s); retry in %ss", settings.temporal_address, exc, delay)
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")
