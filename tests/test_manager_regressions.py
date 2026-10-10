import asyncio
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, MagicMock

import pytest
from engine.game_manager import GameManager, GameTimeoutError
from models.game import Role, CauseOfDeath


def make_manager():
    gm = GameManager(AsyncMock())
    gm.repo = AsyncMock()
    gm._get_memory_summary = AsyncMock(return_value="个人视角")
    gm._get_player_experience = AsyncMock(return_value="")
    return gm


def player(seat, role=Role.villager, human=False):
    return Obj(seat=seat, role=role, is_human=human, is_alive=True, can_vote=True,
               personality="logical", model_name="mock", user_id=None,
               cause_of_death=None, died_at_round=None, nickname=str(seat))


@pytest.mark.asyncio
async def test_immediate_human_response_is_not_lost():
    gm = make_manager()
    async def notify(game_id, kind, data):
        assert gm.resolve_human_action({"prompt_id": data["prompt_id"],
                                       "action_type": "day_vote", "target": 2}) is None
    gm.ws_broadcast = notify
    result = await gm._wait_human_action(1, "day_vote", {"votable_players": [{"seat": 2}]}, timeout=.1)
    assert result["target"] == 2
    assert gm._pending_prompt is None
    assert gm._human_action_future is None


@pytest.mark.asyncio
async def test_notification_commits_before_waiting_on_external_input():
    gm = make_manager()
    async def notify(game_id, kind, data):
        gm.session.commit.assert_awaited_once()
    gm.ws_broadcast = notify
    await gm._notify(1, "info", {"message": "等候玩家"})


@pytest.mark.asyncio
async def test_postgame_experience_does_not_lock_database_during_next_model_call(monkeypatch):
    gm = make_manager()
    roster = [player(1), player(2)]
    for p in roster:
        p.user_id = p.seat
    gm.repo.get_user_by_id.return_value = Obj(experience="")
    gm._get_player_model = AsyncMock(return_value="mock")
    committed_before_calls = []
    async def decide(**kwargs):
        committed_before_calls.append(gm.session.commit.await_count)
        return {"experience": "我想先问清楚具体矛盾。"}
    monkeypatch.setattr("engine.game_manager.ai_decide", decide)
    await gm._update_ai_experiences(Obj(id=1), roster, "good")
    assert committed_before_calls == [0, 1]
    assert len(gm._experience_cache) == 2


@pytest.mark.asyncio
async def test_bad_response_does_not_finish_pending_turn():
    gm = make_manager()
    task = asyncio.create_task(gm._wait_human_action(1, "day_vote", {"votable_players": [{"seat": 2}]}, timeout=1))
    await asyncio.sleep(0)
    assert gm.resolve_human_action({"prompt_id": "stale", "action_type": "day_vote", "target": 2})
    assert not task.done()
    prompt_id = gm._pending_prompt["prompt_id"]
    gm.resolve_human_action({"prompt_id": prompt_id, "action_type": "day_vote", "target": 2})
    await task


@pytest.mark.asyncio
async def test_night_death_notification_never_contains_cause_or_kill_order():
    gm = make_manager()
    roster = {2: player(2), 5: player(5)}
    gm.repo.get_player_by_seat.side_effect = lambda game_id, seat: roster[seat]
    gm.ws_broadcast = AsyncMock()
    deaths = await gm._handle_night_resolve(Obj(id=1, current_round=1), 5, False, 2)
    assert {d["cause"] for d in deaths} == {"wolf_kill", "witch_poison"}
    data = gm.ws_broadcast.call_args.args[2]
    assert data["deaths"] == [{"seat": 2}, {"seat": 5}]
    assert "狼" not in data["message"] and "毒" not in data["message"]


@pytest.mark.asyncio
async def test_ai_last_words_gets_only_public_death_cause(monkeypatch):
    gm = make_manager()
    dead = player(2, Role.villager)
    dead.is_alive = False; dead.cause_of_death = CauseOfDeath.witch_poison
    gm.repo.get_player_by_seat.return_value = dead
    gm.repo.get_alive_players.return_value = [player(3)]
    decide = AsyncMock(return_value={"speech": "我先走了。", "action": {"type": "last_words"}})
    monkeypatch.setattr("engine.game_manager.ai_decide", decide)
    gm._pace_delay = AsyncMock()
    await gm._handle_last_words(Obj(id=1, current_round=1, sheriff_player_seat=None), 2)
    cause = decide.call_args.kwargs["template_vars"]["cause_of_death_desc"]
    assert cause == "夜间死亡（死因未公开）"


@pytest.mark.asyncio
async def test_single_sheriff_candidate_wins_without_a_vote():
    gm = make_manager()
    gm._run_sheriff_vote_round = AsyncMock()
    game = Obj(id=1, current_round=1, sheriff_player_seat=None)
    await gm._handle_sheriff_vote(game, [{"seat": 3, "speech": "竞选"}], {3})
    assert game.sheriff_player_seat == 3
    gm._run_sheriff_vote_round.assert_not_called()


@pytest.mark.asyncio
async def test_abstention_stays_in_public_ballots_without_becoming_a_target(monkeypatch):
    gm = make_manager()
    roster = [player(1, human=True), player(2)]
    gm.repo.get_players.return_value = roster
    gm._wait_human_action = AsyncMock(return_value={"target": None})
    gm._get_player_model = AsyncMock(return_value="mock")
    monkeypatch.setattr("engine.game_manager.ai_decide", AsyncMock(return_value={
        "action": {"type": "vote", "target": None}, "inner_thought": "弃票"}))
    votes = await gm._collect_votes(Obj(id=1, current_round=1, sheriff_player_seat=None), roster, [1, 2], [], "暂无警长")
    assert votes == {1: None, 2: None}
    assert gm._vote_tally(votes, None) == {}


@pytest.mark.asyncio
async def test_pk_voters_exclude_tied_players():
    gm = make_manager()
    roster = [player(1), player(2), player(3), player(4)]
    gm.repo.get_alive_players.return_value = roster
    gm._collect_votes = AsyncMock(side_effect=[{1: 2, 2: 1, 3: 1, 4: 2}, {3: 1, 4: 1}])
    gm._publish_vote = AsyncMock()
    gm._handle_pk_speech = AsyncMock(return_value=[])
    gm._apply_exile = AsyncMock(return_value=(1, False))
    result = await gm._handle_day_vote(Obj(id=1, current_round=1, sheriff_player_seat=None), [])
    assert result == 1
    pk_voters = gm._collect_votes.call_args_list[1].args[1]
    assert [p.seat for p in pk_voters] == [3, 4]


@pytest.mark.asyncio
async def test_timeout_in_night_phase_propagates_to_match_end():
    gm = make_manager()
    gm.repo.get_alive_players.return_value = [player(1)]
    gm.repo.get_players.return_value = []
    gm._handle_wolf_phase = AsyncMock(side_effect=GameTimeoutError())
    game = Obj(id=1, current_round=0)
    with pytest.raises(GameTimeoutError):
        await gm._run_game_loop(game)
