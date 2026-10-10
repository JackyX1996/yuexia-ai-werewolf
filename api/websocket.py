import asyncio
import json
from typing import Any, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from fastapi.encoders import jsonable_encoder
from models.game import Role

router = APIRouter()


class ConnectionManager:
    """Manages WebSocket connections for game rooms."""

    def __init__(self):
        # game_id → WebSocket
        self.connections: dict[int, WebSocket] = {}
        # game_id → GameManager (set when game starts)
        self._game_managers: dict[int, Any] = {}
        # game_id → asyncio.Task (background game task)
        self._game_tasks: dict[int, asyncio.Task] = {}
        self.start_lock = asyncio.Lock()

    async def connect(self, game_id: int, websocket: WebSocket):
        await websocket.accept()
        # Close old connection if exists (reconnect scenario)
        old_ws = self.connections.get(game_id)
        if old_ws is not None:
            try:
                await old_ws.close()
            except Exception:
                pass
        self.connections[game_id] = websocket

    def disconnect(self, game_id: int, websocket=None):
        if websocket is None or self.connections.get(game_id) is websocket:
            self.connections.pop(game_id, None)

    def set_game_manager(self, game_id: int, gm):
        self._game_managers[game_id] = gm

    def set_game_task(self, game_id: int, task: asyncio.Task):
        self._game_tasks[game_id] = task

    async def send(self, game_id: int, message: dict):
        ws = self.connections.get(game_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(game_id, ws)

    async def broadcast(self, game_id: int, msg_type: str, data: dict):
        """Broadcast callback used by GameManager."""
        await self.send(game_id, {"type": msg_type, "data": data})

    def cancel_game_task(self, game_id: int):
        """Cancel the background game task without full cleanup."""
        task = self._game_tasks.pop(game_id, None)
        if task and not task.done():
            task.cancel()
        gm = self._game_managers.get(game_id)
        if gm and gm._human_action_future and not gm._human_action_future.done():
            gm._human_action_future.cancel()

    def cleanup_game(self, game_id: int, expected_task=None):
        """Clean up all resources for a game."""
        registered = self._game_tasks.get(game_id)
        if expected_task is not None and registered is not None and registered is not expected_task:
            return  # A retiring task must not remove its replacement.
        # Cancel the background game task
        task = self._game_tasks.pop(game_id, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        # Cancel any pending human action future
        gm = self._game_managers.get(game_id)
        if gm and gm._human_action_future and not gm._human_action_future.done():
            gm._human_action_future.cancel()
        self._game_managers.pop(game_id, None)


manager = ConnectionManager()


async def _verify_ws_token(token: str, game_id: int = None) -> bool:
    """Verify WebSocket token against DB."""
    if not token:
        return False
    from db.session import async_session
    from db.repository import GameRepository
    async with async_session() as session:
        repo = GameRepository(session)
        sess = await repo.get_session_by_token(token)
        if sess is None:
            return False
        if game_id is not None:
            players = await repo.get_players(game_id)
            return any(p.is_human and p.user_id == sess.user_id for p in players)
        return True


@router.websocket("/ws/games/{game_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: int, token: str = Query(default="")):
    # Verify token
    if not await _verify_ws_token(token, game_id):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await manager.connect(game_id, websocket)

    try:
        # Build connected data with game_started_at for timer
        connected_data = {"game_id": game_id, "message": "已连接到游戏"}

        # Try to get game started_at for timer
        try:
            from db.session import async_session
            from db.repository import GameRepository
            async with async_session() as session:
                repo = GameRepository(session)
                game = await repo.get_game(game_id)
                if game and game.started_at:
                    connected_data["game_started_at"] = game.started_at.isoformat()
                auth_session = await repo.get_session_by_token(token)
                user = await repo.get_user_by_id(auth_session.user_id)
                from api.routes import _build_game_state
                from schemas.game import EventOut
                state = await _build_game_state(session, game_id, user)
                human = next(p for p in state.players if p.is_human)
                events = await repo.get_visible_events(game_id, human.seat, Role(human.role))
                connected_data["state"] = jsonable_encoder(state)
                connected_data["events"] = jsonable_encoder([EventOut.model_validate(e) for e in events])
                task = manager._game_tasks.get(game_id)
                connected_data["interrupted"] = (
                    game.status.value == "running"
                    and game_id not in manager._game_managers
                    and (task is None or task.done())
                )
        except Exception:
            pass

        gm = manager._game_managers.get(game_id)
        connected_data["pending_prompt_id"] = (
            gm._pending_prompt.get("prompt_id") if gm and gm._pending_prompt else None
        )
        await websocket.send_json({
            "type": "connected",
            "data": connected_data,
        })

        # Authenticated reconnect can resume only a completed day boundary.
        # Start after the snapshot so newer phase messages cannot be overwritten.
        from api.routes import resume_completed_day
        async def announce_recovery():
            await websocket.send_json({"type": "game_resumed", "data": {
                "message": "对局已恢复，将从下一个夜晚继续。"}})
        await resume_completed_day(game_id, before_start=announce_recovery)

        # Resend pending prompt on reconnect
        gm = manager._game_managers.get(game_id)
        if gm:
            for msg_type, data in gm.public_turn.items():
                await websocket.send_json({"type": msg_type, "data": data})
        if gm and hasattr(gm, '_pending_prompt') and gm._pending_prompt:
            await websocket.send_json({
                "type": "prompt_action",
                "data": gm._pending_prompt,
            })

        while True:
            data = await websocket.receive_text()
            try:
                action = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "data": {"message": "Invalid JSON"},
                })
                continue

            # Forward action to game manager
            gm = manager._game_managers.get(game_id)
            if gm:
                error = gm.resolve_human_action(action)
                await websocket.send_json({
                    "type": "error" if error else "action_accepted",
                    "data": {"message": error or "操作已提交", "prompt_id": action.get("prompt_id") if isinstance(action, dict) else None},
                })
            else:
                await websocket.send_json({
                    "type": "info",
                    "data": {"message": "等待游戏开始..."},
                })

    except WebSocketDisconnect:
        manager.disconnect(game_id, websocket)
