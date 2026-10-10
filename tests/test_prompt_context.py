import copy
import json
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from ai.graph import _render_template, ai_decide
from ai.turn_context import build_speech_turn
from db.repository import GameRepository
from models.event import Visibility
from models.game import Role
from tests.test_manager_regressions import make_manager, player


ORDER = [1, 12, 10, 8, 7]


@pytest.mark.parametrize("position", range(5))
def test_current_turn_has_only_completed_prefix_and_exact_pending_seats(position):
    heard = [{"seat": s, "speech": "已经说过"} for s in ORDER[:position]]
    turn = build_speech_turn(ORDER[position], ORDER, heard, "sheriff_campaign")
    assert turn["spoken_seats"] == ORDER[:position]
    assert turn["pending_seats"] == ORDER[position + 1:]
    assert turn["self_seat"] not in turn["pending_seats"]


@pytest.mark.parametrize("order,speeches", [
    (ORDER, [{"seat": 1}, {"seat": 8}]),  # Future speech cannot enter seat 12's turn.
    (ORDER, [{"seat": 10}]),
    ([1, 12, 12], [{"seat": 1}]),
    ([1, 10], [{"seat": 1}]),
])
def test_malformed_or_future_speech_evidence_fails_before_a_model_call(order, speeches):
    with pytest.raises(ValueError):
        build_speech_turn(12, order, speeches, "sheriff_campaign")


@pytest.mark.asyncio
async def test_identity_and_legal_example_target_are_authoritative(monkeypatch):
    client = AsyncMock()
    client.chat.return_value = {"action": {"type": "seer_check", "target": 7}}
    monkeypatch.setattr("ai.graph.get_llm_client", lambda _: client)
    await ai_decide(12, Role.seer, None, "seer_night.txt",
                    {"seat": 7, "role": "werewolf"}, "seer_check", [7])
    request = client.chat.call_args.kwargs
    assert "是12号玩家" in request["system_prompt"]
    assert json.loads(request["user_prompt"].split("【本次可执行操作")[0].split("回复JSON格式：")[1])["action"]["target"] == 7


@pytest.mark.parametrize("name", [
    "sheriff_signup", "sheriff_speech", "sheriff_withdraw", "sheriff_vote",
    "day_speech", "day_vote", "seer_night", "werewolf_night", "witch_night",
    "hunter_skill", "last_words", "update_experience",
])
def test_all_stage_json_examples_are_parseable(name):
    prompt = _render_template(name + ".txt", seat=12, role="seer", round=1,
        can_shoot=True, is_sheriff=True, has_antidote=True, wolf_kill_target=None,
        legal_targets=[7], alive_players=[{"seat": 7}])
    example = json.loads(prompt[prompt.rfind('\n{'):])
    assert isinstance(example["inner_thought"], str)
    assert "action" in example or "experience" in example


@pytest.mark.asyncio
async def test_campaign_handler_does_not_precompute_later_claims(monkeypatch):
    gm = make_manager()
    roster = [player(s, human=s == 7) for s in sorted(ORDER)]
    gm.repo.get_alive_players.return_value = roster
    gm._wait_human_action = AsyncMock(side_effect=[
        {"action_type": "sheriff_signup", "run": True}, {"content": "7号最后发言"}])
    gm._pace_delay = AsyncMock()
    monkeypatch.setattr("engine.game_manager.random.choice", lambda choices: choices[0])
    # Explicitly start at 1, then choose counterclockwise.
    choices = iter([1, "counterclockwise"])
    monkeypatch.setattr("engine.game_manager.random.choice", lambda _: next(choices))
    seen = {}
    async def decide(**kwargs):
        if kwargs["action_type"] == "sheriff_signup":
            return {"action": {"run": True}}
        seen[kwargs["seat"]] = copy.deepcopy(kwargs["template_vars"])
        return {"speech": "%d号已公开原话" % kwargs["seat"]}
    monkeypatch.setattr("engine.game_manager.ai_decide", decide)
    await gm._handle_sheriff_campaign(Obj(id=1, current_round=1))
    assert [s["seat"] for s in seen[12]["speeches"]] == [1]
    assert seen[12]["speech_turn"]["pending_seats"] == [10, 8, 7]
    assert [s["seat"] for s in seen[8]["speeches"]] == [1, 12, 10]
    prompt = _render_template("sheriff_speech.txt", **seen[12])
    assert "8号已公开原话" not in prompt
    assert "10号、8号、7号" in prompt


@pytest.mark.asyncio
async def test_private_withdraw_thought_is_not_a_public_withdrawal(monkeypatch):
    gm = make_manager()
    roster = [player(1), player(2)]
    gm.repo.get_alive_players.return_value = roster
    gm.repo.get_players.return_value = roster
    gm.repo.get_all_events.return_value = [
        Obj(event_type="sheriff_campaign", actor_seat=1, content="我争警徽", visibility=Visibility.all),
        Obj(event_type="sheriff_campaign", actor_seat=2, content="我也竞选", visibility=Visibility.all),
        Obj(event_type="sheriff_withdraw", actor_seat=1, content="我不退水", visibility=Visibility.self_only),
        Obj(event_type="sheriff_withdraw", actor_seat=2, content="我退水", visibility=Visibility.all),
    ]
    gm._pace_delay = AsyncMock()
    choices = iter([1, "clockwise"])
    monkeypatch.setattr("engine.game_manager.random.choice", lambda _: next(choices))
    seen = []
    async def decide(**kwargs):
        seen.append(copy.deepcopy(kwargs["template_vars"]))
        return {"speech": "正常白天发言"}
    monkeypatch.setattr("engine.game_manager.ai_decide", decide)
    await gm._handle_day_speech(Obj(id=1, current_round=1, sheriff_player_seat=None))
    assert [(c["seat"], c["withdrew"]) for c in seen[0]["sheriff_speeches"]] == [(1, False), (2, True)]


@pytest.mark.asyncio
async def test_sheriff_pk_labels_previous_pass_separately(monkeypatch):
    gm = make_manager()
    gm.repo.get_alive_players.return_value = [player(7), player(12)]
    gm._pace_delay = AsyncMock()
    choices = iter([7, "clockwise"])
    monkeypatch.setattr("engine.game_manager.random.choice", lambda _: next(choices))
    seen = []
    async def decide(**kwargs):
        seen.append(copy.deepcopy(kwargs["template_vars"]))
        return {"speech": "本次PK发言"}
    monkeypatch.setattr("engine.game_manager.ai_decide", decide)
    previous = [{"seat": s, "speech": "上轮竞选原话"} for s in ORDER]
    await gm._handle_sheriff_tiebreak_speeches(Obj(id=1, current_round=1), [7, 12], previous)
    assert seen[0]["speeches"] == []
    assert seen[0]["previous_speeches"] == previous
    assert seen[0]["speech_turn"]["pending_seats"] == [12]
    assert [s["seat"] for s in seen[1]["speeches"]] == [7]
    assert "上一轮竞选发言（已经结束" in _render_template("sheriff_speech.txt", **seen[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.villager, Role.seer, Role.werewolf])
async def test_repository_keeps_other_private_thoughts_and_system_events_out(role):
    events = [Obj(visibility=v, actor_seat=s) for v,s in [
        (Visibility.all, 1), (Visibility.self_only, 12), (Visibility.self_only, 8),
        (Visibility.system, None), (Visibility.werewolves, 8)]]
    session = AsyncMock()
    session.execute.return_value.scalars = lambda: Obj(all=lambda: events)
    visible = await GameRepository(session).get_visible_events(1, 12, role)
    assert events[0] in visible and events[1] in visible
    assert events[2] not in visible and events[3] not in visible
    assert (events[4] in visible) == (role == Role.werewolf)
