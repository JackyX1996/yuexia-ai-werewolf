from pydantic import field_validator
from pydantic_settings import BaseSettings
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_SQLITE = "sqlite+aiosqlite:///" + os.path.join(_ROOT, "data", "werewolf.db")


class Settings(BaseSettings):
    # Local SQLite. Override with WW_DB_URL if you need another database.
    db_url: str = _DEFAULT_SQLITE

    @property
    def database_url(self) -> str:
        return self.db_url

    # Model names, providers and credentials are managed in the page/database.
    llm_temperature: float = 0.75
    llm_max_tokens: int = 1024
    llm_timeout_seconds: float = 45.0
    # Socket read timeout is idle time for streaming. Bound the whole decision,
    # including retries, separately so a thinking stream cannot wait forever.
    llm_decision_timeout_seconds: float = 120.0
    # Kimi K3 cannot disable thinking. If the answer has not started by this
    # point, stop the stream so the decision budget can fund one shorter retry.
    llm_reasoning_only_seconds: float = 75.0
    llm_audit_path: str = os.path.join(_ROOT, "data", "ai-decisions.jsonl")
    # A character budget is conservative for Chinese across different tokenizers.
    context_memory_chars: int = 6500
    human_action_timeout_seconds: float = 2400.0

    # 12-player 预女猎白: 4 wolves, 4 villagers, seer, witch, hunter, idiot.
    total_players: int = 12
    werewolf_count: int = 4
    villager_count: int = 4

    # Memory compression
    compress_after_rounds: int = 2  # Compress memory every N rounds

    # Fresh databases only. Existing accounts and passwords are left unchanged.
    admin_username: str = "admin"
    admin_password: str = "admin"

    @field_validator("admin_username", "admin_password", mode="before")
    @classmethod
    def blank_admin_uses_default(cls, value):
        if value is None:
            return "admin"
        text = str(value).strip()
        return text or "admin"

    @field_validator("admin_username")
    @classmethod
    def admin_username_shape(cls, value):
        if any(character.isspace() for character in value) or not 1 <= len(value) <= 50:
            raise ValueError("WW_ADMIN_USERNAME 须为 1 到 50 个字符，且不能包含空白")
        return value

    model_config = {"env_prefix": "WW_", "env_file": ".env", "extra": "ignore"}


settings = Settings()
