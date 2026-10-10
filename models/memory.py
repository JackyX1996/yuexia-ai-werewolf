from typing import Optional
from sqlalchemy import BigInteger, SmallInteger, Integer, Text, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from db.session import Base


class PlayerMemory(Base):
    __tablename__ = "player_memory"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("game.id"), nullable=False)
    seat: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    as_of_round: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    suspicion_map: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    key_facts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
