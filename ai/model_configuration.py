"""Runtime snapshot of the database model pool; no environment fallback."""
import os
import asyncio
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import select

PROVIDER_URLS = {
    "bailian": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
}
PROVIDER_NAMES = {"bailian": "百炼", "volcengine": "火山方舟", "openai": "OpenAI 兼容"}


@dataclass(frozen=True)
class ModelConnection:
    id: str
    provider: str
    model_name: str
    api_key: str = field(repr=False)
    base_url: str
    enabled: bool = True
    thinking_enabled: bool = False
    thinking_format: str = "auto"

    def __post_init__(self):
        if self.thinking_enabled:
            raise ValueError("Thinking 固定关闭，不能开启。")


class ModelConfigurationError(ValueError):
    pass


_connections: Dict[str, ModelConnection] = {}
configuration_lock = asyncio.Lock()


def replace_connections(connections: List[ModelConnection]):
    global _connections
    _connections = {connection.id: connection for connection in connections}


def available_connections() -> List[ModelConnection]:
    """The same enabled pool is used for lobby readiness and seat assignment."""
    from ai.model_pool import requires_thinking
    return [connection for connection in _connections.values()
            if connection.enabled and connection.api_key and not requires_thinking(connection.model_name)]


async def load_connections(session):
    from models.model_configuration import ModelConfiguration, ModelPlatformConfiguration
    rows = (await session.execute(select(ModelConfiguration).order_by(ModelConfiguration.id))).scalars().all()
    platforms = {row.id: row for row in (await session.execute(select(ModelPlatformConfiguration))).scalars()}
    replace_connections([ModelConnection(
        id=row.id, provider=platforms[row.platform_id].provider, model_name=row.model_name,
        api_key=platforms[row.platform_id].api_key, base_url=platforms[row.platform_id].base_url,
        enabled=row.enabled and platforms[row.platform_id].enabled,
        thinking_format=platforms[row.platform_id].thinking_format,
    ) for row in rows if row.platform_id in platforms])


async def migrate_platform_connections(session):
    """Group old credentials without changing seat-bound model IDs."""
    from ai.model_pool import requires_thinking
    from models.model_configuration import ModelConfiguration, ModelPlatformConfiguration
    rows = (await session.execute(select(ModelConfiguration).where(
        ModelConfiguration.platform_id.is_(None)).order_by(ModelConfiguration.id))).scalars().all()
    groups = {}
    assignments = []
    for row in rows:
        key = (row.provider, row.base_url, row.api_key, row.thinking_format)
        if key not in groups:
            groups[key] = ModelPlatformConfiguration(
                id=str(uuid.uuid4()), provider=row.provider, base_url=row.base_url,
                api_key=row.api_key, thinking_format=row.thinking_format, enabled=False,
            )
            session.add(groups[key])
        platform = groups[key]
        enabled = row.enabled and not requires_thinking(row.model_name)
        platform.enabled = platform.enabled or enabled
        assignments.append((row, platform, enabled))
    # Insert parents before changing existing child rows, including databases
    # with SQLite foreign-key enforcement enabled.
    await session.flush()
    for row, platform, enabled in assignments:
        row.enabled = enabled
        row.platform_id = platform.id
        row.thinking_enabled = False
        row.provider = row.api_key = row.base_url = ""
    await session.commit()


def resolve_connection(reference: Optional[str] = None) -> ModelConnection:
    if reference in _connections:
        return _connections[reference]
    if reference:
        matches = [connection for connection in _connections.values() if connection.model_name == reference]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ModelConfigurationError("同名模型有多个配置，请创建新对局以绑定具体配置。")
        raise ModelConfigurationError("该座位的模型配置已删除，请在模型配置页面重新添加。")
    available = available_connections()
    if not available:
        raise ModelConfigurationError("请先在大厅的「模型配置」中添加并启用至少一个模型。")
    return available[0]


def pick_connections(count: int, rng=None) -> List[ModelConnection]:
    if count <= 0:
        return []
    chooser = rng or random
    pool = available_connections()
    if not pool:
        raise ModelConfigurationError("请先让管理员在「模型配置」中添加并启用至少一个模型。")
    picked = chooser.sample(pool, min(count, len(pool)))
    while len(picked) < count:
        picked.append(chooser.choice(pool))
    return picked


async def migrate_legacy_connections(session):
    """One-time import when the table is first created, even if later emptied."""
    from dotenv import dotenv_values
    from ai.model_pool import MATCH_MODELS, is_volcengine_model, requires_thinking
    from models.model_configuration import ModelConfiguration

    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env")

    def legacy(name, default=""):
        return (os.environ.get("WW_" + name, values.get("WW_" + name)) or default).strip()

    default_model = legacy("LLM_MODEL")
    names = list(MATCH_MODELS)
    if default_model and default_model not in names:
        names.insert(0, default_model)
    for name in names:
        provider = "volcengine" if is_volcengine_model(name) else "bailian"
        prefix = "VOLCENGINE" if provider == "volcengine" else "DASHSCOPE"
        key = legacy(prefix + "_API_KEY")
        if not key:
            continue
        url = legacy(prefix + "_BASE_URL", PROVIDER_URLS[provider])
        workspace = legacy("DASHSCOPE_WORKSPACE_ID")
        if provider == "bailian" and workspace and "/" in name:
            url = "https://{0}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1".format(workspace)
        thinking_format = "thinking" if provider == "volcengine" else "auto"
        if url.rstrip("/") != PROVIDER_URLS[provider]:
            provider = "openai"
        session.add(ModelConfiguration(
            id=str(uuid.uuid4()), provider=provider, model_name=name, api_key=key,
            base_url=url, enabled=not requires_thinking(name), thinking_enabled=False,
            thinking_format=thinking_format,
        ))
    await session.commit()
    await migrate_platform_connections(session)
