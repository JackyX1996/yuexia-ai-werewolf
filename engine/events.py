"""Extract key events from game event list for summaries."""
from typing import List


def extract_key_events(events) -> List[dict]:
    """从 GameEvent 列表中提取关键事件摘要"""
    key_events = []
    for e in events:
        if e.event_type == "wolf_kill" and e.target_seat:
            key_events.append({"round": e.round, "type": "kill", "text": f"第{e.round}夜 狼人刀了{e.target_seat}号"})
        elif e.event_type == "seer_result":
            key_events.append({"round": e.round, "type": "seer", "text": f"第{e.round}夜 预言家验了{e.target_seat}号（{e.content}）"})
        elif e.event_type == "witch_save":
            key_events.append({"round": e.round, "type": "save", "text": f"第{e.round}夜 女巫救了{e.target_seat}号"})
        elif e.event_type == "witch_poison":
            key_events.append({"round": e.round, "type": "poison", "text": f"第{e.round}夜 女巫毒了{e.target_seat}号"})
        elif e.event_type == "hunter_shot" and e.target_seat:
            key_events.append({"round": e.round, "type": "hunter", "text": f"第{e.round}轮 猎人开枪带走{e.target_seat}号"})
        elif e.event_type == "wolf_explode":
            key_events.append({"round": e.round, "type": "kill", "text": f"第{e.round}轮 {e.actor_seat}号狼人自爆"})
        elif e.event_type == "idiot_reveal" and e.actor_seat:
            key_events.append({"round": e.round, "type": "idiot", "text": f"第{e.round}轮 {e.actor_seat}号白痴翻牌免死，失去投票权"})
        elif e.event_type == "vote_result" and e.target_seat and "白痴" not in (e.content or ""):
            key_events.append({"round": e.round, "type": "vote", "text": f"第{e.round}轮 投票出局：{e.target_seat}号"})
        elif e.event_type == "sheriff_elected" and e.target_seat:
            key_events.append({"round": e.round, "type": "sheriff", "text": f"{e.target_seat}号当选警长"})
        elif e.event_type == "sheriff_transfer" and e.target_seat:
            key_events.append({"round": e.round, "type": "sheriff", "text": f"警徽移交给{e.target_seat}号"})
        elif e.event_type == "sheriff_transfer" and "撕" in (e.content or ""):
            key_events.append({"round": e.round, "type": "sheriff", "text": "警徽被撕掉，警长之位空缺"})
    return key_events
