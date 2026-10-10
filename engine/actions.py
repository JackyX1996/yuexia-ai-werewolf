"""Validate an action against the outstanding prompt, never client role claims."""
from typing import Optional


def action_error(action: dict, prompt: dict) -> Optional[str]:
    if not isinstance(action, dict):
        return "操作格式不正确。"
    if action.get("prompt_id") != prompt.get("prompt_id"):
        return "这次操作已过期，请按当前提示操作。"
    kind = prompt["prompt_type"]
    submitted = action.get("action_type")
    if submitted == "wolf_explode":
        if kind in ("day_speech", "sheriff_speech") and prompt.get("is_wolf"):
            return None
        return "当前不能自爆。"
    if submitted != kind:
        return "操作与当前阶段不匹配。"
    if "content" in action and (not isinstance(action["content"], str) or len(action["content"]) > 500):
        return "发言请控制在500字以内。"
    if kind in ("sheriff_signup", "sheriff_withdraw"):
        key = "run" if kind == "sheriff_signup" else "withdraw"
        if type(action.get(key)) is not bool:
            return "请选择是否参与。"
    elif kind == "speech_direction":
        if action.get("direction") not in ("clockwise", "counterclockwise"):
            return "请选择发言方向。"
    elif kind == "witch_act":
        save = action.get("save", False)
        poison = action.get("poison_target")
        if type(save) is not bool or (save and not prompt.get("can_save")):
            return "当前不能使用解药。"
        if save and poison is not None:
            return "同一晚只能使用一瓶药。"
        if poison is not None and (
            type(poison) is not int or not prompt.get("has_poison")
            or poison not in prompt.get("valid_poison_targets", [])
        ):
            return "毒药目标无效。"
    elif kind in ("wolf_kill", "seer_check", "hunter_shot", "day_vote", "sheriff_vote"):
        choices = prompt.get("valid_targets", [])
        if kind == "day_vote":
            choices = [p["seat"] for p in prompt.get("votable_players", [])]
        elif kind == "sheriff_vote":
            choices = [p["seat"] for p in prompt.get("candidates", [])]
        target = action.get("target")
        optional = kind in ("hunter_shot", "day_vote", "sheriff_vote") or not choices
        if target is None and optional:
            return None
        if type(target) is not int or target not in choices:
            return "请选择当前可操作的玩家。"
    if kind in ("last_words", "sheriff_transfer") and (
        kind == "sheriff_transfer" or prompt.get("is_sheriff")
    ):
        target = action.get("sheriff_transfer")
        alive = [p["seat"] for p in prompt.get("alive_players", [])]
        if target is None or (type(target) is int and target in [0] + alive):
            return None
        return "警徽只能移交给存活玩家，或选择撕掉。"
    return None
