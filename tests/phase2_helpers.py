"""Bounded test-only worker driver; all extraction responses are local fakes."""

from chatbot.config import Settings
from chatbot.providers.base import ProviderRegistry
from chatbot.perf.durable_handlers import make_durable_memory_extract_handler
from chatbot.perf.durable_worker import DurableJobWorker
from test_memory_llm_extraction import FakeProvider


def extraction_registry(response=None):
    provider = FakeProvider(response)
    registry = ProviderRegistry()
    registry.register("openrouter", provider)
    return registry, provider


async def drain_memory_jobs(env, response=None):
    registry, provider = extraction_registry(response)
    worker = DurableJobWorker(
        env.factory,
        handlers={
            "memory_extract": make_durable_memory_extract_handler(
                env.factory,
                registry=registry,
                settings=Settings(_env_file=None),
            ),
        },
    )
    for job in await worker.claim_due_jobs():
        await worker._run_one(job)
    return provider
