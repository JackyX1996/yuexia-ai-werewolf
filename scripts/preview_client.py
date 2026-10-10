"""Deterministic UI fixtures; these are not model-generated decisions."""
import json
import re


class PreviewClient:
    model = "ui-preview"

    async def chat(self, system_prompt, user_prompt, **kwargs):
        legal = json.loads(user_prompt.split("【本次可执行操作，以此为准】\n")[-1].split("\n")[0])
        kind = legal["type"]
        seat = int(re.search(r"是(\d+)号玩家", system_prompt).group(1))
        targets = legal.get("valid_targets") or []
        action = {"type": kind}
        target = targets[-1] if targets else None
        if kind in ("vote", "sheriff_vote", "seer_check", "suggest_kill", "wolf_kill"):
            action["target"] = target
        if kind == "sheriff_signup":
            action["run"] = seat in (1, 3, 7)
        if kind == "sheriff_withdraw":
            action["withdraw"] = False
        if kind == "witch_act":
            action.update(save=False, poison_target=None)
        if kind == "hunter_shot":
            action["target"] = None
        if kind == "last_words":
            action["sheriff_transfer"] = 0

        # Never manufacture a past statement. Night, campaign and day speech
        # have distinct fixtures; a seat's old sample is not reused as evidence.
        speech = ""
        if kind in ("suggest_kill", "wolf_kill"):
            opening = "第一夜还没有公开发言。" if "这是第一个夜晚" in user_prompt else "今晚我先保留对其他人的判断。"
            speech = opening
            if target:
                speech += "我先提议刀%d号，天亮后再看公开发言。上警可以由一位队友争警徽，其他人配合。" % target
        elif kind == "sheriff_speech":
            speech = "我想争警徽。目前公开信息还很少，我会先记录发言和票型；如果当选，会把归票的依据交代清楚。"
        elif kind == "speech":
            lines = (
                "我暂时没有确定的狼坑，想先听清每个人的依据，再决定这票。身份声明先当成说法，不能直接当作事实。",
                "我这轮先保留判断。比起谁的语气更坚定，我更在意依据能不能对得上；信息不足时，宁可慢一点。",
                "我不急着站边。先把公开的信息记清楚，有新依据我会改判断，也会交代为什么改。",
            )
            speech = lines[(seat - 1) % len(lines)]
        elif kind == "last_words":
            speech = "我出局了。这局自己的判断还不够稳，你们继续把公开发言和票型放在一起看吧。"

        return {
            "speech": speech,
            "inner_thought": "这是界面预览的模拟决策，不代表真实模型推理。",
            "action": action,
            "experience": "我习惯先记录明确依据，再决定投票，信息不足时会暂缓判断。",
        }
