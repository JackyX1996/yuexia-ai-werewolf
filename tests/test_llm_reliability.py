"""Thinking stream completion, cancellation, total budget and safe diagnostics."""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APITimeoutError

from ai.diagnostics import LLMResponseError, call_metrics, decision_game_id, update_call_metrics
from ai.graph import ai_decide
from tests.llm_transport_helpers import kimi_transport_client
from ai.llm_client import LLMClient
from config import settings
from models.game import Role


def chunk(content=None, reasoning=None, finish=None):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
        delta=SimpleNamespace(content=content, reasoning_content=reasoning))])


class DelayedStream:
    def __init__(self, values):
        self.values = values
        self.closed = False

    async def __aiter__(self):
        for delay, value in self.values:
            await asyncio.sleep(delay)
            yield value

    async def close(self):
        self.closed = True


def kimi_with_stream(stream):
    client = kimi_transport_client()
    client.client = AsyncMock()
    client.client.chat.completions.create.return_value = stream
    return client


def audit_entries():
    return [json.loads(line) for line in Path(settings.llm_audit_path).read_text().splitlines()]


@pytest.mark.asyncio
async def test_reasoning_progress_is_consumed_without_becoming_an_answer():
    stream = DelayedStream([(0, chunk(reasoning="私有推理，不是公开台词")),
                            (0.01, chunk('{"speech": "')), (0.01, chunk('完成", "action": {"type":"speech"}}', finish="stop"))])
    client = kimi_with_stream(stream)
    metrics = {}
    token = call_metrics.set(metrics)
    try:
        result = await client.chat("private system", "private user")
    finally:
        call_metrics.reset(token)
    assert result["speech"] == "完成"
    assert metrics["first_answer_seconds"] >= 0.009
    assert metrics["reasoning_chars"] == len("私有推理，不是公开台词")
    assert "私有推理" not in json.dumps(metrics, ensure_ascii=False)
    assert stream.closed


@pytest.mark.asyncio
async def test_direct_stream_call_is_bounded_without_game_decision_wrapper(monkeypatch):
    stream = DelayedStream([(0, chunk(reasoning="thinking")), (10, chunk(finish="stop"))])
    client = kimi_with_stream(stream)
    monkeypatch.setattr(settings, "llm_decision_timeout_seconds", 0.02)
    with pytest.raises(asyncio.TimeoutError):
        await client.chat("system", "JSON")
    assert stream.closed


@pytest.mark.asyncio
async def test_continuous_stream_still_has_a_total_deadline_and_closes(monkeypatch):
    stream = DelayedStream([(0, chunk(reasoning="still thinking")),
                            (10, chunk('{"speech":"late"}', finish="stop"))])
    client = kimi_with_stream(stream)
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    monkeypatch.setattr(settings, "llm_decision_timeout_seconds", 0.02)
    result = await ai_decide(11, Role.seer, "logical", "day_speech.txt", {"round": 1}, "speech",
                             model_name="kimi-k3", max_retries=2)
    assert result["inner_thought"] == "（系统回退决策）"
    assert stream.closed
    assert client.client.chat.completions.create.await_count == 1
    entry = audit_entries()[-1]
    assert entry["outcome"] == "fallback"
    assert entry["failures"][0]["category"] == "decision_deadline"
    assert entry["failures"][0]["transport"]["chunks"] == 1


@pytest.mark.asyncio
async def test_reasoning_only_stream_stops_before_the_decision_budget(monkeypatch):
    stream = DelayedStream([(0.01, chunk(reasoning="仍在想")) for _ in range(20)])
    client = kimi_with_stream(stream)
    monkeypatch.setattr(settings, "llm_reasoning_only_seconds", 0.03)
    monkeypatch.setattr(settings, "llm_decision_timeout_seconds", 5)
    with pytest.raises(LLMResponseError) as caught:
        await client.chat("system", "JSON")
    assert caught.value.category == "reasoning_overrun"
    assert stream.closed


@pytest.mark.asyncio
async def test_reasoning_overrun_retries_with_a_short_prompt(monkeypatch):
    prompts = []

    async def chat(system_prompt, user_prompt, json_mode=True):
        prompts.append(user_prompt)
        if len(prompts) == 1:
            raise LLMResponseError("reasoning_overrun", "still thinking")
        return {"inner_thought": "看票", "speech": "3号的票型和原话对不上。",
                "action": {"type": "speech"}}

    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: SimpleNamespace(model="kimi-k3", chat=chat))
    result = await ai_decide(
        10, Role.idiot, "silent", "day_speech.txt",
        {"round": 2, "speeches": [{"seat": 3, "content": "只有我投了12号"}]},
        "speech", model_name="kimi-k3",
    )
    assert result["speech"] == "3号的票型和原话对不上。"
    assert len(prompts) == 2
    assert "没有写出最终答案" in prompts[1]
    assert "3号：只有我投了12号" in prompts[1]
    assert audit_entries()[-1]["outcome"] == "success"


@pytest.mark.asyncio
async def test_retry_uses_remaining_budget_instead_of_restarting_deadline(monkeypatch):
    calls = []

    async def fail_then_wait(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            await asyncio.sleep(0.015)
            raise APITimeoutError(request=httpx.Request("POST", "https://example.test"))
        await asyncio.sleep(10)

    client = SimpleNamespace(model="kimi-k3", chat=fail_then_wait)
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    monkeypatch.setattr(settings, "llm_decision_timeout_seconds", 0.035)
    start = time.monotonic()
    result = await ai_decide(11, Role.seer, "logical", "day_speech.txt", {"round": 1}, "speech")
    assert time.monotonic() - start < 0.3
    assert len(calls) == 2
    assert result["inner_thought"] == "（系统回退决策）"
    assert [f["category"] for f in audit_entries()[-1]["failures"]] == ["request_timeout", "decision_deadline"]


@pytest.mark.asyncio
async def test_cancellation_does_not_manufacture_a_fallback_and_closes_stream(monkeypatch):
    stream = DelayedStream([(0, chunk(reasoning="still thinking")), (10, chunk(finish="stop"))])
    client = kimi_with_stream(stream)
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    task = asyncio.create_task(ai_decide(11, Role.seer, "logical", "day_speech.txt", {"round": 1}, "speech"))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed
    assert not Path(settings.llm_audit_path).exists()


@pytest.mark.asyncio
async def test_persisted_failures_distinguish_json_and_fields_without_saving_secrets(monkeypatch):
    client = AsyncMock()
    client.model = "kimi-k3"
    client.chat.side_effect = [json.JSONDecodeError("private response", "SECRET-OUTPUT", 0),
                              {"speech": None, "action": {"type": "speech"}}]
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    result = await ai_decide(11, Role.seer, "logical", "day_speech.txt",
                             {"round": 1, "memory_summary": "SECRET-CONTEXT"}, "speech")
    assert result["inner_thought"] == "（系统回退决策）"
    text = Path(settings.llm_audit_path).read_text()
    assert "SECRET" not in text and "private response" not in text
    final = audit_entries()[-1]
    assert [f["category"] for f in final["failures"]] == ["invalid_json", "validation_error"]
    assert final["seat"] == 11 and final["template"] == "day_speech.txt"
    assert len({entry["decision_id"] for entry in audit_entries()}) == 1


@pytest.mark.asyncio
async def test_parallel_calls_keep_transport_metadata_isolated(monkeypatch):
    async def chat(system_prompt, **kwargs):
        marker = "seer" if "真实身份：预言家" in system_prompt else "villager"
        update_call_metrics(marker=marker)
        await asyncio.sleep(0.01)
        return {"speech": "完成", "action": {"type": "speech"}}

    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: SimpleNamespace(model="test", chat=chat))
    await asyncio.gather(
        ai_decide(11, Role.seer, "logical", "day_speech.txt", {"round": 1}, "speech"),
        ai_decide(5, Role.villager, "logical", "day_speech.txt", {"round": 1}, "speech"))
    by_seat = {e["seat"]: e["transport"] for e in audit_entries()}
    assert by_seat == {11: {"marker": "seer"}, 5: {"marker": "villager"}}


@pytest.mark.asyncio
async def test_unwritable_audit_does_not_turn_valid_speech_into_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "llm_audit_path", str(tmp_path))
    client = AsyncMock()
    client.model = "test"
    client.chat.return_value = {"speech": "正常发言", "action": {"type": "speech"}}
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    assert (await ai_decide(11, Role.seer, "logical", "day_speech.txt", {"round": 1}, "speech"))["speech"] == "正常发言"


@pytest.mark.asyncio
async def test_game_scope_is_tagged_and_reset_even_after_failure(monkeypatch):
    from engine.game_manager import GameManager
    client = AsyncMock()
    client.model = "test"
    client.chat.return_value = {"speech": "正常发言", "action": {"type": "speech"}}
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    gm = GameManager(AsyncMock())
    gm.repo = SimpleNamespace(get_game=AsyncMock(return_value=SimpleNamespace(id=42)),
                              update_game=AsyncMock())

    async def loop(game):
        assert decision_game_id.get() == 42
        await ai_decide(11, Role.seer, "logical", "day_speech.txt", {"round": 1}, "speech")
        raise RuntimeError("test failure")

    gm._run_game_loop = loop
    with pytest.raises(RuntimeError, match="test failure"):
        await gm.run_game(42)
    assert decision_game_id.get() is None
    assert audit_entries()[-1]["game_id"] == 42
