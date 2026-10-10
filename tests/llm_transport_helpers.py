"""Exercise retained stream parsing independently of the live fixed-off pool."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from ai.llm_client import LLMClient


def kimi_transport_client():
    client = object.__new__(LLMClient)
    client.model = "kimi-k3"
    client.connection = SimpleNamespace(provider="bailian")
    client.extra_body = {}
    client.supports_json_mode = True
    client.client = AsyncMock()
    return client
