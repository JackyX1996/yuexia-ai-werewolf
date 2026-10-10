"""Safe day boundary recovery and progression without duplicate game actions."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from engine.game_manager import GameManager
from engine.recovery import completed_day_checkpoint
from models.event import Visibility
from models.game import CauseOfDeath, GameStatus, Role
from api.websocket import ConnectionManager


def board():
    roles = [Role.werewolf] * 4 + [Role.villager] * 4 + [Role.seer, Role.witch, Role.hunter, Role.idiot]
    return [Obj(seat=i + 1, role=role, is_alive=i != 4, can_vote=True,
                died_at_round=1 if i == 4 else None, cause_of_death=CauseOfDeath.vote if i == 4 else None,
                is_human=i == 0, model_name="mock", nickname=str(i + 1), personality="logical", user_id=None)
            for i, role in enumerate(roles)]


def game():
    return Obj(id=5, status=GameStatus.running, current_round=1, current_phase="day_vote",
               sheriff_player_seat=9, config={}, started_at="original-start")


def event(number, kind, actor=None, target=None, visibility=Visibility.all):
    return Obj(id=number, round=1, event_type=kind, actor_seat=actor,
               target_seat=target, visibility=visibility)


def events():
    return [event(1, "wolf_kill", target=6, visibility=Visibility.werewolves),
            event(10, "vote_result", target=5), event(11, "last_words", actor=5)]


def test_old_completed_exile_can_resume_and_preserves_last_knife():
    assert completed_day_checkpoint(game(), board(), events()) == {
        "kind": "day_complete", "round": 1, "last_wolf_target": 6,
        "source": "completed_exile_events"}


@pytest.mark.parametrize("pending", ["no_words", "private_words", "other_words", "hunter", "sheriff", "alive", "wrong_death", "wrong_round", "pk", "night", "finished"])
def test_recovery_refuses_unfinished_or_ambiguous_actions(pending):
    g, roster, history = game(), board(), events()
    if pending == "no_words": history.pop()
    elif pending == "private_words": history[-1].visibility = Visibility.self_only
    elif pending == "other_words": history[-1].actor_seat = 6
    elif pending == "hunter": roster[4].role = Role.hunter
    elif pending == "sheriff": g.sheriff_player_seat = 5
    elif pending == "alive": roster[4].is_alive = True
    elif pending == "wrong_death": roster[4].cause_of_death = CauseOfDeath.witch_poison
    elif pending == "wrong_round": roster[4].died_at_round = 0
    elif pending == "pk": history[-2].target_seat = None
    elif pending == "night": g.current_phase = "wolf_kill"
    elif pending == "finished": g.status = GameStatus.finished
    assert completed_day_checkpoint(g, roster, history) is None


def test_explicit_checkpoint_resumes_peaceful_day_but_never_wrong_round_or_phase():
    g = game(); g.current_phase = "win_check"
    g.config = {"resume_checkpoint": {"kind": "day_complete", "round": 1, "last_wolf_target": 6}}
    assert completed_day_checkpoint(g, board(), []) == g.config["resume_checkpoint"]
    g.current_round = 2
    assert completed_day_checkpoint(g, board(), []) is None
    g.current_round = 1; g.current_phase = "last_words"
    assert completed_day_checkpoint(g, board(), []) is None


def test_won_game_does_not_resume_into_another_night():
    roster = board()
    for p in roster[:4]: p.is_alive = False
    assert completed_day_checkpoint(game(), roster, events()) is None


class ReachedNextNight(BaseException):
    pass


@pytest.mark.asyncio
async def test_first_day_vote_and_last_words_progress_to_second_night_with_checkpoint(monkeypatch):
    gm = GameManager(AsyncMock()); gm.repo = AsyncMock(); gm.ws_broadcast = AsyncMock()
    g = game(); g.current_round = 0
    roster = board(); roster[4].is_alive = True; roster[4].cause_of_death = None; roster[4].died_at_round = None
    gm.repo.get_players.return_value = roster
    gm.repo.get_alive_players.side_effect = lambda _: [p for p in roster if p.is_alive]
    gm.repo.get_player_by_seat.side_effect = lambda game_id, seat: next(p for p in roster if p.seat == seat)
    checkpoints = []
    async def update(game, **fields):
        for key, value in fields.items(): setattr(game, key, value)
        if "resume_checkpoint" in fields.get("config", {}):
            checkpoints.append(dict(fields["config"]["resume_checkpoint"]))
    async def kill(p, cause, round_num):
        p.is_alive = False; p.cause_of_death = cause; p.died_at_round = round_num
    gm.repo.update_game.side_effect = update; gm.repo.kill_player.side_effect = kill
    async def wolf(game, **kwargs):
        if game.current_round == 2: raise ReachedNextNight()
        return 6
    gm._handle_wolf_phase = wolf
    gm._handle_seer_phase = AsyncMock(); gm._handle_witch_phase = AsyncMock(return_value=(False, None))
    gm._handle_sheriff_campaign = AsyncMock(return_value=[]); gm._handle_sheriff_vote = AsyncMock()
    gm._handle_night_resolve = AsyncMock(return_value=[]); gm._handle_day_speech = AsyncMock(return_value=[])
    gm._collect_votes = AsyncMock(return_value={p.seat: 5 if p.seat != 5 else None for p in roster})
    gm._get_memory_summary = AsyncMock(return_value="个人记忆"); gm._get_player_experience = AsyncMock(return_value="")
    gm._get_player_model = AsyncMock(return_value="mock"); gm._pace_delay = AsyncMock()
    decide = AsyncMock(return_value={"speech": "我已经被投出去了。", "action": {"type": "last_words"}})
    monkeypatch.setattr("engine.game_manager.ai_decide", decide)
    with pytest.raises(ReachedNextNight): await gm._run_game_loop(g)
    phases = [c.args[2]["phase"] for c in gm.ws_broadcast.call_args_list if c.args[1] == "phase_change"]
    tail = phases[phases.index("day_vote"):]
    assert tail == ["day_vote", "vote_resolve", "last_words", "win_check", "night_start", "wolf_kill"]
    assert checkpoints == [{"kind": "day_complete", "round": 1, "last_wolf_target": 6}]
    assert g.current_round == 2 and "resume_checkpoint" not in g.config
    assert roster[4].is_alive is False
    kinds = [c.args[3] for c in gm.repo.add_event.call_args_list]
    assert kinds.count("vote_result") == 1 and kinds.count("last_words") == 1
    decide.assert_awaited_once()


@pytest.mark.asyncio
async def test_resumed_loop_enters_next_night_once_without_replaying_exile_or_resetting_timer():
    gm = GameManager(AsyncMock()); gm.repo = AsyncMock()
    g = game(); g.current_phase = "win_check"
    g.config["resume_checkpoint"] = {"kind": "day_complete", "round": 1, "last_wolf_target": 6}
    gm.repo.get_game.return_value = g

    async def update(game, **fields):
        for key, value in fields.items(): setattr(game, key, value)
    gm.repo.update_game.side_effect = update
    observed = []
    async def wolf(game, last_wolf_target=None):
        observed.append((game.current_round, game.current_phase, last_wolf_target, dict(game.config)))
        raise ReachedNextNight()
    gm._handle_wolf_phase = wolf
    gm._handle_day_vote = AsyncMock(); gm._handle_last_words = AsyncMock()
    gm.ws_broadcast = AsyncMock()
    with pytest.raises(ReachedNextNight):
        await gm.run_game(5, resume_after_day=True)
    assert observed == [(2, "wolf_kill", 6, {})]
    assert g.started_at == "original-start"
    gm._handle_day_vote.assert_not_called(); gm._handle_last_words.assert_not_called()
    gm.repo.add_event.assert_not_called()
    phases = [call.args[2]["phase"] for call in gm.ws_broadcast.call_args_list if call.args[1] == "phase_change"]
    assert phases == ["night_start", "wolf_kill"]


@pytest.mark.asyncio
async def test_parallel_reconnects_launch_one_task_and_announce_before_new_prompts(monkeypatch):
    import api.routes as routes
    import db.session as sessions
    manager = ConnectionManager(); repo = AsyncMock(); g = game()
    repo.get_game.return_value = g; repo.get_players.return_value = board(); repo.get_all_events.return_value = events()
    order = []
    @asynccontextmanager
    async def session_factory(): yield AsyncMock()
    async def runner(game_id, resume_after_day=False):
        order.append("runner")
        assert resume_after_day
        await asyncio.Event().wait()
    async def announce(): order.append("announce")
    monkeypatch.setattr(sessions, "async_session", session_factory)
    monkeypatch.setattr(routes, "GameRepository", lambda _: repo)
    monkeypatch.setattr(routes, "ws_manager", manager)
    monkeypatch.setattr(routes, "_run_game_background", runner)
    results = await asyncio.gather(routes.resume_completed_day(5, announce), routes.resume_completed_day(5, announce))
    await asyncio.sleep(0)
    assert results == [True, False]
    assert order == ["announce", "runner"]
    assert len(manager._game_tasks) == 1
    fields = repo.update_game.call_args.kwargs
    assert fields["current_phase"] == "win_check"
    assert fields["config"]["resume_checkpoint"]["round"] == 1
    task = manager._game_tasks[5]; task.cancel()
    with pytest.raises(asyncio.CancelledError): await task


@pytest.mark.asyncio
async def test_completed_background_task_closes_session_without_cancelling_itself(monkeypatch):
    import api.routes as routes
    import db.session as sessions
    manager = ConnectionManager(); closed = []
    @asynccontextmanager
    async def session_factory():
        yield AsyncMock()
        closed.append(True)
    gm = Obj(run_game=AsyncMock(), _human_action_future=None)
    monkeypatch.setattr(sessions, "async_session", session_factory)
    monkeypatch.setattr(routes, "GameManager", lambda *a, **kw: gm)
    monkeypatch.setattr(routes, "ws_manager", manager)
    task = asyncio.create_task(routes._run_game_background(5))
    manager.set_game_task(5, task)
    await task
    assert not task.cancelled() and closed == [True]
    assert 5 not in manager._game_tasks and 5 not in manager._game_managers


@pytest.mark.asyncio
async def test_background_session_exit_error_is_recorded_and_reported(monkeypatch):
    import api.routes as routes
    import db.session as sessions
    manager = ConnectionManager(); manager.broadcast = AsyncMock()
    @asynccontextmanager
    async def session_factory():
        yield AsyncMock()
        raise RuntimeError("PRIVATE-PAYLOAD")
    gm = Obj(run_game=AsyncMock(), _human_action_future=None)
    records = []
    monkeypatch.setattr(sessions, "async_session", session_factory)
    monkeypatch.setattr(routes, "GameManager", lambda *a, **kw: gm)
    monkeypatch.setattr(routes, "ws_manager", manager)
    monkeypatch.setattr(routes, "record_decision", records.append)
    task = asyncio.create_task(routes._run_game_background(5)); manager.set_game_task(5, task)
    await task
    assert records[-1]["outcome"] == "failed" and records[-1]["exception"] == "RuntimeError"
    assert "PRIVATE-PAYLOAD" not in str(manager.broadcast.call_args)
    assert "PRIVATE-PAYLOAD" not in str(records)


@pytest.mark.asyncio
async def test_manual_cancel_still_cleans_manager_after_task_is_removed(monkeypatch):
    import api.routes as routes
    import db.session as sessions
    manager = ConnectionManager()
    @asynccontextmanager
    async def session_factory(): yield AsyncMock()
    gm = Obj(run_game=AsyncMock(side_effect=asyncio.CancelledError()), _human_action_future=None)
    monkeypatch.setattr(sessions, "async_session", session_factory)
    monkeypatch.setattr(routes, "GameManager", lambda *a, **kw: gm)
    monkeypatch.setattr(routes, "ws_manager", manager)
    task = asyncio.create_task(routes._run_game_background(5))
    # cancel_game_task removes the registered task before the coroutine's finally.
    with pytest.raises(asyncio.CancelledError): await task
    assert 5 not in manager._game_managers
