from pydantic import BaseModel
from typing import Optional, Any


class WSMessage(BaseModel):
    """Server → Client message."""
    type: str  # phase_change, speech, death, vote_result, prompt_action, game_end, error, info
    data: dict[str, Any] = {}


class WSAction(BaseModel):
    """Client → Server message."""
    action_type: str  # vote, use_skill, speech, sheriff_campaign, sheriff_vote
    target: Optional[int] = None  # target seat
    content: Optional[str] = None  # speech content
