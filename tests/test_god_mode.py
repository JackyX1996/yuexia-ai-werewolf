"""God mode fills every seat with AI and keeps the human as an observer."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai.model_configuration import ModelConfigurationError
from db.repository import GameRepository
from db.session import Base
from engine.game_manager import GameManager
from models.event import Visibility
from models.game import Game, GameStatus
from tests.test_model_configuration import connection, replace_connections


@pytest.mark.asyncio
async def test_god_mode_seats_twelve_ai_and_records_the_observer():
    first, second = connection("first"), connection("second")
    replace_connections([first, second])
    gm = GameManager(AsyncMock())
    gm.repo = AsyncMock()
    gm.repo.create_game.return_value = SimpleNamespace(id=9)
    gm.repo.get_ai_users.return_value = []

    await gm.create_game(human_user_id=7, mode="god")

    assert gm.repo.create_game.call_args.kwargs["config"] == {
        "mode": "god", "observer_user_id": 7,
    }
    seats = [call.kwargs for call in gm.repo.add_player.call_args_list]
    assert len(seats) == 12
    assert all(not seat["is_human"] for seat in seats)
    assert all(seat["model_config_id"] in {"first", "second"} for seat in seats)
    assert gm.repo.get_ai_users.call_args.args == (12,)


@pytest.mark.asyncio
async def test_god_mode_still_refuses_to_open_without_a_model():
    replace_connections([])
    gm = GameManager(AsyncMock())
    gm.repo = AsyncMock()
    with pytest.raises(ModelConfigurationError):
        await gm.create_game(human_user_id=7, mode="god")
    gm.repo.create_game.assert_not_called()


@pytest.mark.asyncio
async def test_private_events_reach_only_a_watching_god():
    gm = GameManager(AsyncMock())
    sent = []

    async def broadcast(game_id, msg_type, data):
        sent.append((game_id, msg_type, data))

    gm.ws_broadcast = broadcast
    gm.god_mode = True
    private = SimpleNamespace(
        game_id=3, round=1, phase="seer_check", event_type="seer_result",
        actor_seat=2, target_seat=5, content="好人", visibility=Visibility.self_only,
    )
    public = SimpleNamespace(
        game_id=3, round=1, phase="day_speech", event_type="day_speech",
        actor_seat=2, target_seat=None, content="公开", visibility=Visibility.all,
    )
    await gm._relay_god_event(private)
    await gm._relay_god_event(public)
    gm.god_mode = False
    await gm._relay_god_event(private)

    assert len(sent) == 1
    assert sent[0][0] == 3 and sent[0][1] == "god_event"
    assert sent[0][2]["content"] == "好人"
    assert sent[0][2]["visibility"] == "self"


@pytest.mark.asyncio
async def test_observer_can_find_a_god_game_and_a_stranger_cannot(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "games.db"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        for game_id, config in (
            (1, {"mode": "god", "observer_user_id": 7}),
            (2, {"mode": "god", "observer_user_id": 8}),
            (3, {"mode": "seat"}),
        ):
            session.add(Game(
                id=game_id, status=GameStatus.waiting, current_phase="created",
                current_round=0, config=config,
            ))
        await session.commit()
        mine, total = await GameRepository(session).get_games_by_user(7)
    await engine.dispose()

    assert total == 1
    assert len(mine) == 1
    assert mine[0].config["observer_user_id"] == 7
    assert mine[0].status == GameStatus.waiting
