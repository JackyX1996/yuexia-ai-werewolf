"""Lineup sampling and per-model call shape. No network."""
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from tests.llm_transport_helpers import kimi_transport_client
from ai.llm_client import LLMClient, _message_text
from ai.model_pool import (
    MATCH_MODELS,
    extra_body_for,
    is_volcengine_model,
    max_tokens_for,
    pick_match_models,
    supports_json_mode,
    temperature_for,
    streams_reasoning_for,
)


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def close(self):
        self.closed = True


def stream_chunk(content=None, reasoning=None, finish=None):
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish,
        delta=SimpleNamespace(content=content, reasoning_content=reasoning))])


class TestPickMatchModels:
    def test_eight_seats_are_distinct(self):
        rng = random.Random(7)
        picked = pick_match_models(8, rng)
        assert len(picked) == 8
        assert len(set(picked)) == 8
        assert set(picked).issubset(set(MATCH_MODELS))

    def test_seed_is_stable(self):
        assert pick_match_models(8, random.Random(1)) == pick_match_models(8, random.Random(1))

    def test_two_seeds_can_differ(self):
        a = pick_match_models(8, random.Random(1))
        b = pick_match_models(8, random.Random(2))
        assert a != b

    def test_retired_models_stay_out_of_the_lineup(self):
        retired = {"kimi-k3", "qwen3.7-flash"}
        assert retired.isdisjoint(MATCH_MODELS)
        assert len(MATCH_MODELS) >= 11
        assert retired.isdisjoint(pick_match_models(11, random.Random(0)))


class TestRouting:
    def test_volcengine_allow_list(self):
        assert is_volcengine_model("doubao-seed-2-1-pro-260915")
        assert is_volcengine_model("deepseek-v4-1-flash-260910")
        assert "deepseek-v4-flash-0731" in MATCH_MODELS
        assert "deepseek-v4-flash" not in MATCH_MODELS
        assert not is_volcengine_model("deepseek-v4-flash-0731")
        assert not is_volcengine_model("deepseek-v4-pro")
        assert not is_volcengine_model("MiniMax-M3")
        assert not is_volcengine_model("stepfun/step-3.7-flash")
        assert not is_volcengine_model("glm-5.2")

    def test_thinking_flags(self):
        with pytest.raises(ValueError, match="不支持关闭 Thinking"):
            extra_body_for("glm-5.3")
        with pytest.raises(ValueError, match="固定关闭"):
            extra_body_for("glm-5.3", thinking_enabled=True)
        assert extra_body_for("glm-5.2") == {"enable_thinking": False}
        assert extra_body_for("qwen3.7-flash") == {"enable_thinking": False}
        with pytest.raises(ValueError, match="不支持关闭 Thinking"):
            extra_body_for("kimi-k3")
        with pytest.raises(ValueError, match="不支持关闭 Thinking"):
            extra_body_for("kimi/kimi-k3")
        assert extra_body_for("deepseek-v4-flash-0731") == {"enable_thinking": False}
        assert extra_body_for("stepfun/step-3.7-flash") == {"enable_thinking": False}
        with pytest.raises(ValueError, match="不支持关闭 Thinking"):
            extra_body_for("MiniMax-M2.5")
        assert extra_body_for("MiniMax-M3") == {"thinking": {"type": "disabled"}}
        assert extra_body_for("MiniMax/MiniMax-M3") == {"thinking": {"type": "disabled"}}
        assert extra_body_for("doubao-seed-evolving") == {"thinking": {"type": "disabled"}}
        assert extra_body_for("deepseek-v4-1-flash-260910") == {"thinking": {"type": "disabled"}}

    def test_json_and_budget(self):
        assert supports_json_mode("glm-5.2")
        assert supports_json_mode("deepseek-v4-pro")
        assert not supports_json_mode("MiniMax-M2.5")
        assert not supports_json_mode("MiniMax-M3")
        assert max_tokens_for("MiniMax-M2.5", 1024) == 4096
        assert max_tokens_for("glm-5.3", 1024) == 2048
        assert max_tokens_for("qwen3.7-plus", 1024) == 1024
        assert max_tokens_for("kimi-k3", 1024) == 8192
        assert temperature_for("kimi-k3", .75) == 1.0
        assert temperature_for("qwen3.7-plus", .75) == .75


class TestMessageText:
    def test_strips_think_and_falls_back(self):
        class Msg:
            content = "<think>secret</think>\n{\"action\":{}}"
            reasoning_content = None

        assert _message_text(Msg()) == '{"action":{}}'

    def test_empty_content_uses_reasoning(self):
        class Msg:
            content = None
            reasoning_content = "<think>hmm</think>{\"ok\":1}"

        assert _message_text(Msg()) == '{"ok":1}'


@pytest.mark.asyncio
@pytest.mark.parametrize("multi_turn", [False, True])
async def test_kimi_request_never_sends_unsupported_temperature_or_thinking_flag(multi_turn):
    client = kimi_transport_client()
    client.client = AsyncMock()
    stream = FakeStream([stream_chunk(reasoning='{"ok":0}'),
                         stream_chunk('{"ok":'), stream_chunk('1}', finish="stop")])
    client.client.chat.completions.create.return_value = stream
    if multi_turn:
        result = await client.chat_multi_turn("system", [{"role": "user", "content": "JSON"}], temperature=.2)
    else:
        result = await client.chat("system", "JSON", temperature=.75)
    assert result == {"ok": 1}
    sent = client.client.chat.completions.create.call_args.kwargs
    assert sent["temperature"] == 1.0
    assert "extra_body" not in sent
    assert sent["max_tokens"] >= 8192
    assert sent["stream"] is True
    assert stream.closed


@pytest.mark.asyncio
async def test_truncated_thinking_response_is_not_adopted_as_a_final_decision():
    client = kimi_transport_client()
    client.client = AsyncMock()
    stream = FakeStream([stream_chunk(reasoning='{"action":{"type":"sheriff_signup","run":false}}', finish="length")])
    client.client.chat.completions.create.return_value = stream
    with pytest.raises(ValueError, match="truncated"):
        await client.chat("system", "JSON")
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("finish,content,category", [
    (None, '{"ok":1}', "incomplete_stream"),
    ("length", '{"ok":1}', "truncated"),
    ("stop", None, "empty_response"),
])
async def test_kimi_never_adopts_unfinished_json_or_reasoning_as_answer(finish, content, category):
    client = kimi_transport_client()
    client.client = AsyncMock()
    stream = FakeStream([stream_chunk(content, reasoning='{"ok":1}', finish=finish)])
    client.client.chat.completions.create.return_value = stream
    with pytest.raises(ValueError) as error:
        await client.chat("system", "JSON")
    assert error.value.category == category
    assert stream.closed


@pytest.mark.asyncio
async def test_other_models_keep_nonstream_request_policy():
    client = LLMClient("qwen3.7-plus")
    client.client = AsyncMock()
    client.client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"ok":1}'))])
    assert await client.chat("system", "JSON") == {"ok": 1}
    assert "stream" not in client.client.chat.completions.create.call_args.kwargs
    assert streams_reasoning_for("kimi/kimi-k3")
    assert not streams_reasoning_for("qwen3.7-plus")
