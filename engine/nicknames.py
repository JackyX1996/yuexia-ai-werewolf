"""Nickname pool for AI Werewolf players."""
import random
from typing import List

NICKNAME_POOL = [
    "佛系青年", "干饭王", "显眼包", "老六", "大冤种",
    "卷王", "摸鱼达人", "社牛", "社恐", "嘴替",
    "气氛组", "工具人", "海王", "咸鱼", "戏精",
    "杠精", "柠檬精", "鸽王", "肝帝", "欧皇",
    "非酋", "吃瓜群众", "键盘侠", "白月光", "小透明",
    "话痨", "冷场王", "暖场王", "带节奏", "躺平族",
    "内卷怪", "摆烂王", "整活儿", "抬杠王",
]


def assign_nicknames(count: int) -> List[str]:
    """Randomly pick `count` unique nicknames from the pool."""
    pool = list(NICKNAME_POOL)
    if count > len(pool):
        # Shouldn't happen with 9 players, but be safe
        pool = pool * ((count // len(pool)) + 1)
    return random.sample(pool, count)
