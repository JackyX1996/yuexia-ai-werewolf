from pydantic import BaseModel, field_validator
from typing import Optional, List
from datetime import datetime


class GameCreate(BaseModel):
    mode: str = "seat"

    @field_validator("mode")
    @classmethod
    def known_mode(cls, value):
        if value not in ("seat", "god"):
            raise ValueError("mode 只能是 seat 或 god")
        return value


class PlayerInfo(BaseModel):
    seat: int
    is_human: bool
    is_alive: bool
    role: Optional[str] = None  # Only revealed to the player themselves or after game
    personality: Optional[str] = None
    nickname: Optional[str] = None
    model_name: Optional[str] = None
    can_vote: bool = True

    model_config = {"from_attributes": True}


class GameState(BaseModel):
    id: int
    status: str
    mode: str = "seat"
    current_phase: str
    current_round: int
    winner: Optional[str] = None
    sheriff_player_seat: Optional[int] = None
    players: list[PlayerInfo] = []
    created_at: datetime
    started_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class GameListItem(BaseModel):
    id: int
    status: str
    mode: str = "seat"
    winner: Optional[str] = None
    current_round: int
    my_role: Optional[str] = None
    my_seat: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class GameListResponse(BaseModel):
    items: List[GameListItem]
    total: int
    page: int
    page_size: int


class EventOut(BaseModel):
    id: int
    round: int
    phase: str
    event_type: str
    actor_seat: Optional[int] = None
    target_seat: Optional[int] = None
    content: Optional[str] = None
    visibility: Optional[str] = None
    created_at: datetime

    @field_validator("visibility", mode="before")
    @classmethod
    def visibility_text(cls, value):
        if value is None:
            return None
        return getattr(value, "value", value)

    model_config = {"from_attributes": True}
