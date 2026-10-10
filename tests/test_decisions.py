from unittest.mock import AsyncMock
import pytest
from ai.graph import _render_template, _validate_action, ai_decide
import json
from models.game import Role


@pytest.mark.parametrize("target", [True, "3", 7, None])
def test_seer_cannot_accept_invalid_or_empty_target(target):
    assert not _validate_action({"action": {"type": "seer_check", "target": target}}, "seer_check", [3])


def test_votes_can_abstain_and_speeches_must_be_strings():
    assert _validate_action({"action": {"type": "vote", "target": None}}, "vote", [2])
    assert not _validate_action({"action": {"type": "vote"}}, "vote", [2])
    assert not _validate_action({"speech": {"text": "bad"}, "action": {"type": "speech"}}, "speech")
    assert not _validate_action({"speech": "", "action": {"type": "speech"}}, "speech")


@pytest.mark.asyncio
async def test_invalid_output_gets_repair_instructions_without_adopting_it(monkeypatch):
    client = AsyncMock()
    client.chat.side_effect = [
        {"action": {"type": "seer_check", "target": 9}, "inner_thought": "invented fact"},
        {"action": {"type": "seer_check", "target": 3}, "inner_thought": "choose 3"},
    ]
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    result = await ai_decide(1, Role.seer, "logical", "seer_night.txt",
                             {"alive_players": [{"seat": 3}], "seat": 1},
                             "seer_check", valid_targets=[3])
    assert result["action"]["target"] == 3
    prompt = client.chat.call_args_list[1].kwargs["user_prompt"]
    assert "上次输出校验失败" in prompt
    assert "invented fact" not in prompt


@pytest.mark.asyncio
async def test_failure_abstains_instead_of_biasing_low_number_seat(monkeypatch):
    client = AsyncMock()
    client.chat.side_effect = ValueError("unavailable")
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    result = await ai_decide(1, Role.villager, "silent", "day_vote.txt", {}, "vote", [2, 3])
    assert result["action"]["target"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cause,is_sheriff", [("被投票出局", False), ("夜间死亡（死因未公开）", True)])
async def test_failed_last_words_still_respects_elimination_and_private_death(cause, is_sheriff, monkeypatch):
    client = AsyncMock()
    client.chat.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    context = {"seat": 3, "cause_of_death_desc": cause, "is_sheriff": is_sheriff,
               "alive_players": [{"seat": 2}, {"seat": 8}]}
    result = await ai_decide(3, Role.villager, "silent", "last_words.txt", context, "last_words")
    assert _validate_action(result, "last_words", context=context)
    assert "先听大家" not in result["speech"]
    assert "投出去了" in result["speech"] if cause == "被投票出局" else "已经出局" in result["speech"]
    assert "狼刀" not in result["speech"] and "被毒" not in result["speech"]
    if is_sheriff:
        assert result["action"]["sheriff_transfer"] == 0


@pytest.mark.asyncio
async def test_last_words_retry_identifies_the_invalid_field_without_replaying_bad_output(monkeypatch):
    client = AsyncMock()
    client.chat.side_effect = [
        {"inner_thought": {"evidence": "invented secret"}, "speech": "告别。", "action": {"type": "last_words"}},
        {"inner_thought": "我已经出局", "speech": "我被投出去了，你们接着盘。", "action": {"type": "last_words"}},
    ]
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    result = await ai_decide(3, Role.villager, "silent", "last_words.txt", {}, "last_words")
    assert result["speech"] == "我被投出去了，你们接着盘。"
    prompt = client.chat.call_args_list[1].kwargs["user_prompt"]
    assert "inner_thought必须是字符串" in prompt
    assert "invented secret" not in prompt
    assert "已出局，不能再投票" in prompt


@pytest.mark.parametrize("is_sheriff", [True, False])
def test_last_words_template_demonstrates_parseable_json(is_sheriff):
    prompt = _render_template("last_words.txt", is_sheriff=is_sheriff)
    example = json.loads(prompt[prompt.rfind('\n{'):])
    assert example["action"]["type"] == "last_words"
    if is_sheriff:
        assert type(example["action"]["sheriff_transfer"]) is int


@pytest.mark.asyncio
@pytest.mark.parametrize("role,expected", [(Role.seer, True), (Role.villager, False)])
async def test_signup_failure_has_a_legal_action_and_does_not_silence_the_seer(role, expected, monkeypatch):
    client = AsyncMock()
    client.chat.side_effect = RuntimeError("unavailable")
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    result = await ai_decide(3, role, "logical", "sheriff_signup.txt", {}, "sheriff_signup")
    assert _validate_action(result, "sheriff_signup")
    assert result["action"]["run"] is expected
