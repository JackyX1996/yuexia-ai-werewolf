from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class GameCreate(BaseModel):
    pass


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
    created_at: datetime

    model_config = {"from_attributes": True}
