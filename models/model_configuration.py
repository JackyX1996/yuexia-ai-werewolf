"""Persisted model connections. API keys are never included in API responses."""
from typing import Optional
from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from db.session import Base


class ModelPlatformConfiguration(Base):
    """One endpoint and credential shared by any number of model entries."""
    __tablename__ = "model_platform_configuration"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    thinking_format: Mapped[str] = mapped_column(String(30), default="auto", nullable=False)


class ModelConfiguration(Base):
    __tablename__ = "model_configuration"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    platform_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("model_platform_configuration.id"), nullable=True, index=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Retained for SQLite upgrades. After migration the credential lives only
    # on the platform; these legacy columns are cleared and never read.
    provider: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    api_key: Mapped[str] = mapped_column(Text, default="", nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    thinking_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    thinking_format: Mapped[str] = mapped_column(String(30), default="auto", nullable=False)
