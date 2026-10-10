"""Durable call metadata, never prompts, roles, model thoughts or credentials."""
import json
import logging
import threading
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

from openai import APIConnectionError, APIStatusError, APITimeoutError
from config import settings


call_metrics = ContextVar("llm_call_metrics", default=None)
decision_game_id = ContextVar("llm_decision_game_id", default=None)
_handlers = {}
_lock = threading.Lock()
logger = logging.getLogger(__name__)


def update_call_metrics(**values):
    current = call_metrics.get()
    if current is not None:
        current.update(values)


def failure_category(error: Exception) -> str:
    if isinstance(error, APITimeoutError):
        return "request_timeout"
    if isinstance(error, APIStatusError):
        return "http_error"
    if isinstance(error, APIConnectionError):
        return "connection_error"
    if isinstance(error, json.JSONDecodeError):
        return "invalid_json"
    return getattr(error, "category", None) or "response_error"


class LLMResponseError(ValueError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


def record_decision(entry: Dict[str, Any]):
    """Write bounded, rotating JSONL without coupling logging to game progress."""
    path = settings.llm_audit_path
    if not path:
        return
    try:
        with _lock:
            handler = _handlers.get(path)
            if handler is None:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024,
                                              backupCount=2, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(message)s"))
                _handlers[path] = handler
        message = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        handler.handle(logging.LogRecord("ai.decision.audit", logging.INFO,
                                        __file__, 0, message, (), None))
    except (OSError, ValueError, TypeError):
        logger.warning("AI decision audit file unavailable")
