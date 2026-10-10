import enum
from datetime import datetime
from typing import Optional
from sqlalchemy import BigInteger, String, SmallInteger, Integer, Enum, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from db.session import Base


class Visibility(str, enum.Enum):
    all = "all"
    werewolves = "werewolves"
    self_only = "self"
    system = "system"


class GameEvent(Base):
    __tablename__ = "game_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("game.id"), nullable=False)
    round: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(50), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_seat: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    target_seat: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility), default=Visibility.all, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
