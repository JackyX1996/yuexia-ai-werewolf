import enum
from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    BigInteger, String, Boolean, SmallInteger, Integer, Enum, JSON, DateTime, ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db.session import Base


class GameStatus(str, enum.Enum):
    waiting = "waiting"
    running = "running"
    finished = "finished"


class Winner(str, enum.Enum):
    good = "good"
    evil = "evil"


class Role(str, enum.Enum):
    villager = "villager"
    werewolf = "werewolf"
    seer = "seer"
    witch = "witch"
    hunter = "hunter"
    idiot = "idiot"


class CauseOfDeath(str, enum.Enum):
    wolf_kill = "wolf_kill"
    vote = "vote"
    witch_poison = "witch_poison"
    hunter_shot = "hunter_shot"
    wolf_explode = "wolf_explode"


class Game(Base):
    __tablename__ = "game"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    status: Mapped[GameStatus] = mapped_column(
        Enum(GameStatus), default=GameStatus.waiting, nullable=False
    )
    winner: Mapped[Optional[Winner]] = mapped_column(Enum(Winner), nullable=True)
    current_phase: Mapped[str] = mapped_column(String(50), default="created")
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    sheriff_player_seat: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    players: Mapped[List["GamePlayer"]] = relationship(back_populates="game", lazy="selectin")


class GamePlayer(Base):
    __tablename__ = "game_player"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("game.id"), nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("user.id"), nullable=True)
    seat: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False)
    is_human: Mapped[bool] = mapped_column(Boolean, default=False)
    is_alive: Mapped[bool] = mapped_column(Boolean, default=True)
    personality: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    nickname: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # False after the idiot flips on exile. They stay alive but cannot vote.
    can_vote: Mapped[bool] = mapped_column(Boolean, default=True)
    # Model drawn for this seat in this game. User.model_name is only a fallback.
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model_config_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    cause_of_death: Mapped[Optional[CauseOfDeath]] = mapped_column(
        Enum(CauseOfDeath), nullable=True
    )
    died_at_round: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    game: Mapped["Game"] = relationship(back_populates="players")


class WitchState(Base):
    __tablename__ = "witch_state"

    game_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("game.id"), primary_key=True
    )
    has_antidote: Mapped[bool] = mapped_column(Boolean, default=True)
    has_poison: Mapped[bool] = mapped_column(Boolean, default=True)
