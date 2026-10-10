"""Stub db.session before any test module imports ORM models."""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# Must run before any import of models.*
if "db.session" not in sys.modules:
    _fake_db = types.ModuleType("db")
    _fake_db.__path__ = [str(Path(__file__).resolve().parents[1] / "db")]
    _fake_session_mod = types.ModuleType("db.session")

    # Provide a real DeclarativeBase so ORM mapped_column works
    from sqlalchemy.orm import DeclarativeBase

    class _TestBase(DeclarativeBase):
        pass

    _fake_session_mod.Base = _TestBase
    _fake_session_mod.engine = MagicMock()
    _fake_session_mod.async_session = MagicMock()
    _fake_session_mod.init_db = MagicMock()
    _fake_session_mod.get_session = MagicMock()

    sys.modules["db"] = _fake_db
    sys.modules["db.session"] = _fake_session_mod


@pytest.fixture(autouse=True)
def isolated_model_connections(monkeypatch):
    from ai import model_configuration
    from ai.model_configuration import ModelConnection, PROVIDER_URLS
    from ai.model_pool import MATCH_MODELS, is_volcengine_model, requires_thinking
    from ai.llm_client import _client_cache
    connections = {}
    for name in MATCH_MODELS + ["kimi-k3", "kimi/kimi-k3"]:
        provider = "volcengine" if is_volcengine_model(name) else "bailian"
        connections[name] = ModelConnection(
            id=name, provider=provider, model_name=name,
            api_key="test-key", base_url=PROVIDER_URLS[provider],
            enabled=not requires_thinking(name),
        )
    monkeypatch.setattr(model_configuration, "_connections", connections)
    _client_cache.clear()
    yield
    _client_cache.clear()


@pytest.fixture(autouse=True)
def isolated_ai_audit(monkeypatch, tmp_path):
    from config import settings
    path = str(tmp_path / "ai-decisions.jsonl")
    monkeypatch.setattr(settings, "llm_audit_path", path)
    yield
    from ai.diagnostics import _handlers
    handler = _handlers.pop(path, None)
    if handler is not None:
        handler.close()
