import json

import pytest

from scripts.preview_client import PreviewClient


def prompt(kind, text="", targets=None):
    return text + "\n【本次可执行操作，以此为准】\n" + json.dumps({
        "type": kind, "valid_targets": targets or [],
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("seat", [3, 7, 11])
async def test_first_night_does_not_quote_nonexistent_day_speeches(seat):
    result = await PreviewClient().chat(
        "你是%d号玩家" % seat,
        prompt("suggest_kill", "这是第一个夜晚。之前的讨论为空。", [1, 12]),
    )
    assert "还没有公开发言" in result["speech"]
    assert "刚才" not in result["speech"]
    assert "退水" not in result["speech"]
    assert "归票" not in result["speech"]
    assert result["action"] == {"type": "suggest_kill", "target": 12}


@pytest.mark.asyncio
async def test_no_same_day_accusation_reused_across_phases():
    client = PreviewClient()
    night = await client.chat("你是3号玩家", prompt("suggest_kill", "这是第一个夜晚", [12]))
    day = await client.chat("你是3号玩家", prompt("speech", "本轮还没有其他玩家发言"))
    assert night["speech"] != day["speech"]
    assert "7号" not in day["speech"]
    assert "刚才" not in day["speech"]


@pytest.mark.asyncio
async def test_preview_long_term_habits_do_not_add_other_game_facts():
    result = await PreviewClient().chat("你是3号玩家", prompt("any"))
    assert "号" not in result["experience"]
    assert "刚才" not in result["experience"]
