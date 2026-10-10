from typing import Optional
from dataclasses import dataclass
from models.game import Role


@dataclass
class RoleConfig:
    name_cn: str
    team: str  # "good" or "evil"
    category: str  # "god", "villager", "werewolf"
    description: str
    night_action: bool


ROLE_CONFIGS: dict[Role, RoleConfig] = {
    Role.villager: RoleConfig(
        name_cn="平民",
        team="good",
        category="villager",
        description="没有特殊技能，通过白天发言和投票帮助好人阵营获胜。",
        night_action=False,
    ),
    Role.werewolf: RoleConfig(
        name_cn="狼人",
        team="evil",
        category="werewolf",
        description="夜晚可以与同伴商议并选择一名玩家击杀。白天需要伪装身份，避免被投票出局。",
        night_action=True,
    ),
    Role.seer: RoleConfig(
        name_cn="预言家",
        team="good",
        category="god",
        description="每晚可以查验一名玩家的身份（好人/狼人）。是好人阵营的核心信息源。",
        night_action=True,
    ),
    Role.witch: RoleConfig(
        name_cn="女巫",
        team="good",
        category="god",
        description="拥有一瓶解药（救人）和一瓶毒药（毒人），整局各限用一次。夜晚可以使用。",
        night_action=True,
    ),
    Role.hunter: RoleConfig(
        name_cn="猎人",
        team="good",
        category="god",
        description="被投票出局或被狼人击杀时（非毒杀），可以开枪带走一名玩家。被开枪的人没有遗言。",
        night_action=False,
    ),
    Role.idiot: RoleConfig(
        name_cn="白痴",
        team="good",
        category="god",
        description="被投票放逐时可以翻牌免死，但之后失去投票权。夜里死亡则技能不发动。",
        night_action=False,
    ),
}


def get_role_name(role: Role) -> str:
    return ROLE_CONFIGS[role].name_cn


def get_role_team(role: Role) -> str:
    return ROLE_CONFIGS[role].team


def is_god_role(role: Role) -> bool:
    return ROLE_CONFIGS[role].category == "god"


def can_hunter_shoot(cause_of_death: Optional[str]) -> bool:
    """Hunter can shoot unless killed by witch poison."""
    return cause_of_death != "witch_poison"


def idiot_survives_exile(role: Role, can_vote: bool) -> bool:
    """Idiot flips once, on a day exile, and only if the skill is still unused."""
    return role == Role.idiot and can_vote


def get_standard_role_list() -> list[Role]:
    """12-player 预女猎白: 4 wolves, 4 villagers, seer, witch, hunter, idiot."""
    return [
        Role.werewolf, Role.werewolf, Role.werewolf, Role.werewolf,
        Role.villager, Role.villager, Role.villager, Role.villager,
        Role.seer, Role.witch, Role.hunter, Role.idiot,
    ]
