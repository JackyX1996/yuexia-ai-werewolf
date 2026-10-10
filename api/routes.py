import asyncio
import os
import traceback
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session
from db.repository import GameRepository
from engine.game_manager import GameManager
from schemas.game import GameCreate, GameState, PlayerInfo, EventOut, GameListItem, GameListResponse
from models.game import GameStatus, Role
from models.user import User
from api.auth import get_current_user
from api.websocket import manager as ws_manager
from engine.events import extract_key_events
from engine.recovery import completed_day_checkpoint
from ai.diagnostics import record_decision
from ai.model_configuration import ModelConfigurationError

router = APIRouter()


@router.get("/runtime")
async def runtime_info(request: Request):
    """Expose the execution mode, never credentials or provider configuration."""
    return {"ai_mode": getattr(request.app.state, "ai_mode", "live")}


async def require_game_player(repo, game_id: int, user: User):
    players = await repo.get_players(game_id)
    player = next((p for p in players if p.is_human and p.user_id == user.id), None)
    if not player:
        raise HTTPException(403, "你不能访问其他玩家的对局")
    return player


@router.post("/games", response_model=GameState)
async def create_game(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    gm = GameManager(session, ws_broadcast=ws_manager.broadcast)
    try:
        game = await gm.create_game(human_user_id=user.id)
    except ModelConfigurationError as error:
        raise HTTPException(400, str(error))
    return await _build_game_state(session, game.id, user)


@router.get("/games", response_model=GameListResponse)
async def list_games(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    repo = GameRepository(session)
    games, total = await repo.get_games_by_user(user.id, page=page, page_size=page_size)
    items = []
    for g in games:
        human_player = next((p for p in g.players if p.user_id == user.id), None)
        items.append(GameListItem(
            id=g.id,
            status=g.status.value,
            winner=g.winner.value if g.winner else None,
            current_round=g.current_round,
            my_role=human_player.role.value if human_player else None,
            my_seat=human_player.seat if human_player else None,
            created_at=g.created_at,
            started_at=g.started_at,
        ))
    return GameListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/games/{game_id}", response_model=GameState)
async def get_game(
    game_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await _build_game_state(session, game_id, user)


@router.post("/games/{game_id}/start")
async def start_game(
    game_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    async with ws_manager.start_lock:
        repo = GameRepository(session)
        game = await repo.get_game(game_id)
        if not game:
            raise HTTPException(404, "Game not found")
        await require_game_player(repo, game_id, user)
        if game.status != GameStatus.waiting:
            raise HTTPException(400, "Game already started")

        existing = ws_manager._game_tasks.get(game_id)
        if existing and not existing.done():
            raise HTTPException(409, "Game already starting")
        # Mark started before launching; concurrent start requests cannot duplicate a match.
        await repo.update_game(game, status=GameStatus.running)
        await session.commit()
        task = asyncio.create_task(_run_game_background(game_id))
        ws_manager.set_game_task(game_id, task)
        return {"status": "started", "game_id": game_id}


@router.get("/games/{game_id}/events", response_model=list[EventOut])
async def get_events(
    game_id: int,
    round: int = None,
    seat: int = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    repo = GameRepository(session)
    game = await repo.get_game(game_id)
    if not game:
        raise HTTPException(404, "Game not found")

    player = await require_game_player(repo, game_id, user)
    if seat is not None and seat != player.seat:
        raise HTTPException(403, "只能查看自己的游戏视角")
    if game.status == GameStatus.finished:
        events = await repo.get_all_events(game_id, round)
    else:
        events = await repo.get_visible_events(game_id, player.seat, player.role, round)

    return [EventOut.model_validate(e) for e in events]


@router.get("/games/{game_id}/review")
async def get_review(
    game_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    repo = GameRepository(session)
    game = await repo.get_game(game_id)
    if not game:
        raise HTTPException(404, "Game not found")
    await require_game_player(repo, game_id, user)
    if game.status != GameStatus.finished:
        raise HTTPException(400, "Game not finished yet")

    players = await repo.get_players(game_id)
    events = await repo.get_all_events(game_id)

    # Prefer the model drawn for this game. Old rows fall back to the user record.
    model_map = {}
    for p in players:
        if p.model_name:
            model_map[p.seat] = p.model_name
        elif p.user_id:
            u = await repo.get_user_by_id(p.user_id)
            if u and u.model_name:
                model_map[p.seat] = u.model_name

    return {
        "game_id": game_id,
        "winner": game.winner.value if game.winner else None,
        "rounds": game.current_round,
        "players": [
            {
                "seat": p.seat,
                "role": p.role.value,
                "is_human": p.is_human,
                "is_alive": p.is_alive,
                "personality": p.personality,
                "nickname": p.nickname,
                "cause_of_death": p.cause_of_death.value if p.cause_of_death else None,
                "died_at_round": p.died_at_round,
                "model_name": model_map.get(p.seat),
            }
            for p in players
        ],
        "events": [EventOut.model_validate(e).model_dump() for e in events],
        "key_events": extract_key_events(events),
    }


@router.post("/games/{game_id}/end")
async def end_game(
    game_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    repo = GameRepository(session)
    game = await repo.get_game(game_id)
    if not game:
        raise HTTPException(404, "Game not found")
    await require_game_player(repo, game_id, user)
    if game.status == GameStatus.finished:
        raise HTTPException(400, "Game already finished")

    # Cancel the background task
    ws_manager.cancel_game_task(game_id)

    # Mark game as finished
    await repo.update_game(game, status=GameStatus.finished.value)
    await session.commit()

    # Build player list for game over screen
    players = await repo.get_players(game_id)
    player_data = [
        {
            "seat": p.seat,
            "role": p.role.value,
            "is_human": p.is_human,
            "is_alive": p.is_alive,
            "nickname": p.nickname,
            "model_name": p.model_name,
        }
        for p in players
    ]

    await ws_manager.broadcast(game_id, "game_end", {
        "winner_text": "游戏已手动结束",
        "players": player_data,
    })

    return {"ok": True}


async def _build_game_state(session: AsyncSession, game_id: int, user: User = None) -> GameState:
    repo = GameRepository(session)
    game = await repo.get_game(game_id)
    if not game:
        raise HTTPException(404, "Game not found")

    players = await repo.get_players(game_id)
    human_player = await require_game_player(repo, game_id, user) if user else None

    player_infos = []
    for p in players:
        info = PlayerInfo(
            seat=p.seat,
            is_human=p.is_human,
            is_alive=p.is_alive,
            personality=p.personality,
            nickname=p.nickname,
            model_name=p.model_name,
            can_vote=bool(p.can_vote),
        )
        if p.role == Role.idiot and not p.can_vote:
            info.role = p.role.value
        # Reveal role to the human player, their wolf teammates, or after game ends
        if (human_player and p.id == human_player.id) or game.status == GameStatus.finished:
            info.role = p.role.value
        elif (human_player and human_player.role == Role.werewolf
              and p.role == Role.werewolf):
            info.role = p.role.value
        player_infos.append(info)

    return GameState(
        id=game.id,
        status=game.status.value,
        current_phase=game.current_phase,
        current_round=game.current_round,
        winner=game.winner.value if game.winner else None,
        sheriff_player_seat=game.sheriff_player_seat,
        players=player_infos,
        created_at=game.created_at,
        started_at=game.started_at,
    )


async def resume_completed_day(game_id: int, before_start=None) -> bool:
    """Called only after WebSocket ownership verification; never duplicate a task."""
    from db.session import async_session
    async with ws_manager.start_lock:
        task = ws_manager._game_tasks.get(game_id)
        if game_id in ws_manager._game_managers or (task is not None and not task.done()):
            return False
        async with async_session() as session:
            repo = GameRepository(session)
            game = await repo.get_game(game_id)
            if game is None or game.status != GameStatus.running:
                return False
            checkpoint = completed_day_checkpoint(game, await repo.get_players(game_id),
                                                  await repo.get_all_events(game_id))
            if checkpoint is None:
                return False
            config = dict(game.config or {})
            config["resume_checkpoint"] = checkpoint
            await repo.update_game(game, config=config, current_phase="win_check")
            await session.commit()
        if before_start is not None:
            await before_start()
        task = asyncio.create_task(_run_game_background(game_id, resume_after_day=True))
        ws_manager.set_game_task(game_id, task)
        record_decision({"kind": "game_task", "game_id": game_id,
                         "outcome": "resumed_day_boundary", "round": checkpoint["round"],
                         "timestamp": datetime.now(timezone.utc).isoformat()})
        return True


async def _run_game_background(game_id: int, resume_after_day: bool = False):
    """Run game in a background task with its own session."""
    from db.session import async_session
    outcome = "completed"
    error_class = None
    location = []
    gm = None
    record_decision({"kind": "game_task", "game_id": game_id, "outcome": "started",
                     "resumed": resume_after_day, "timestamp": datetime.now(timezone.utc).isoformat()})
    try:
        async with async_session() as session:
            gm = GameManager(session, ws_broadcast=ws_manager.broadcast)
            ws_manager.set_game_manager(game_id, gm)
            if resume_after_day:
                await gm.run_game(game_id, resume_after_day=True)
            else:
                await gm.run_game(game_id)
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as e:
        outcome = "failed"
        error_class = type(e).__name__
        location = [{"file": os.path.basename(frame.filename), "line": frame.lineno,
                     "function": frame.name} for frame in traceback.extract_tb(e.__traceback__)[-8:]]
        print(f"Game {game_id} flow failed: {error_class}")
        await ws_manager.broadcast(game_id, "error", {
            "message": "对局流程已中断，请重连尝试恢复。历史发言和投票已保留。"})
    finally:
        record_decision({"kind": "game_task", "game_id": game_id, "outcome": outcome,
                         "exception": error_class, "location": location,
                         "phase": getattr(gm, "diagnostic_phase", None),
                         "round": getattr(gm, "diagnostic_round", None),
                         "timestamp": datetime.now(timezone.utc).isoformat()})
        ws_manager.cleanup_game(game_id, expected_task=asyncio.current_task())
