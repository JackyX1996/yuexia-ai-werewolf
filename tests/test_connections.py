from unittest.mock import AsyncMock

import pytest
from api.websocket import ConnectionManager


@pytest.mark.asyncio
async def test_old_socket_disconnect_cannot_remove_new_connection():
    manager = ConnectionManager()
    old, new = AsyncMock(), AsyncMock()
    await manager.connect(1, old)
    await manager.connect(1, new)
    manager.disconnect(1, old)
    await manager.send(1, {"type": "info"})
    new.send_json.assert_awaited_once()
    assert manager.connections[1] is new


@pytest.mark.asyncio
async def test_failed_old_send_cannot_remove_replacement_connection():
    manager = ConnectionManager()
    old, new = AsyncMock(), AsyncMock()
    manager.connections[1] = old
    async def replace_then_fail(message):
        manager.connections[1] = new
        raise ConnectionError()
    old.send_json.side_effect = replace_then_fail
    await manager.send(1, {"type": "info"})
    assert manager.connections[1] is new


@pytest.mark.asyncio
async def test_retiring_game_task_cannot_clean_up_its_replacement():
    manager = ConnectionManager()
    old, replacement = AsyncMock(), AsyncMock()
    manager.set_game_task(1, replacement)
    gm = AsyncMock(); manager.set_game_manager(1, gm)
    manager.cleanup_game(1, expected_task=old)
    assert manager._game_tasks[1] is replacement
    assert manager._game_managers[1] is gm
    replacement.cancel.assert_not_called()
