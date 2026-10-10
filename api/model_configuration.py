"""Administrator-managed shared endpoints and per-model completion probes."""
import asyncio
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from openai import APIConnectionError, APIStatusError, APITimeoutError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, require_admin
from ai.llm_client import LLMClient
from ai.model_configuration import ModelConnection, available_connections, configuration_lock, load_connections
from ai.model_pool import extra_body_for, requires_thinking
from db.session import get_session
from models.model_configuration import ModelConfiguration, ModelPlatformConfiguration
from models.user import User
from schemas.model_configuration import ModelConfigurationInput

router = APIRouter(prefix="/model-configurations")


@router.get("/status")
async def model_pool_status(request: Request, user: User = Depends(get_current_user)):
    ai_mode = getattr(request.app.state, "ai_mode", "live")
    count = len(available_connections())
    return {"available_count": count, "can_start": ai_mode == "simulated" or count > 0,
            "ai_mode": ai_mode}


async def require_model_admin(user: User = Depends(require_admin)):
    return user


def masked_api_key(api_key):
    if not api_key:
        return None
    if len(api_key) <= 16:
        return "********"
    return api_key[:6] + "******" + api_key[-4:]


def public_configuration(platform, models):
    return {
        "id": platform.id, "provider": platform.provider,
        "model_names": [row.model_name for row in models],
        "models": [{"id": row.id, "model_name": row.model_name,
                    "enabled": platform.enabled and row.enabled and not requires_thinking(row.model_name),
                    "unavailable_reason": "不支持关闭 Thinking" if requires_thinking(row.model_name) else None}
                   for row in models],
        "base_url": platform.base_url, "has_api_key": bool(platform.api_key),
        "api_key_masked": masked_api_key(platform.api_key),
        "enabled": platform.enabled, "thinking_enabled": False,
        "thinking_format": platform.thinking_format,
    }


async def get_configuration(session, configuration_id):
    row = await session.get(ModelPlatformConfiguration, configuration_id)
    if row is None:
        raise HTTPException(404, "平台配置不存在，请刷新页面")
    return row


async def get_models(session, platform_id):
    return list((await session.execute(select(ModelConfiguration).where(
        ModelConfiguration.platform_id == platform_id).order_by(ModelConfiguration.model_name))).scalars())


async def configuration_in_use(session, row):
    from sqlalchemy import or_, and_
    from models.game import Game, GamePlayer, GameStatus
    return (await session.execute(
        select(GamePlayer.id).join(Game, Game.id == GamePlayer.game_id).where(
            or_(GamePlayer.model_config_id == row.id,
                and_(GamePlayer.model_config_id.is_(None), GamePlayer.model_name == row.model_name)),
            Game.status.in_([GameStatus.waiting, GameStatus.running]),
        ).limit(1)
    )).first() is not None


def api_key_from_input(data, existing=None):
    api_key = data.api_key.get_secret_value().strip() if data.api_key else ""
    if not api_key and existing and existing.provider == data.provider and existing.base_url == data.base_url:
        api_key = existing.api_key
    if not api_key:
        raise HTTPException(422, "请填写 API Key；切换平台或 Base URL 后需重新填写")
    if any(character.isspace() for character in api_key):
        raise HTTPException(422, "API Key 不能包含空白字符")
    return api_key


@router.get("")
async def list_configurations(user=Depends(require_model_admin), session: AsyncSession = Depends(get_session)):
    platforms = (await session.execute(select(ModelPlatformConfiguration).order_by(
        ModelPlatformConfiguration.provider, ModelPlatformConfiguration.id))).scalars().all()
    models = (await session.execute(select(ModelConfiguration).order_by(ModelConfiguration.model_name))).scalars().all()
    by_platform = {}
    for row in models:
        by_platform.setdefault(row.platform_id, []).append(row)
    return [public_configuration(platform, by_platform.get(platform.id, [])) for platform in platforms]


@router.post("")
async def create_configuration(data: ModelConfigurationInput, user=Depends(require_model_admin),
                               session: AsyncSession = Depends(get_session)):
    async with configuration_lock:
        platform = ModelPlatformConfiguration(
            id=str(uuid.uuid4()), provider=data.provider, api_key=api_key_from_input(data),
            base_url=data.base_url, enabled=data.enabled, thinking_format=data.thinking_format)
        session.add(platform)
        await session.flush()
        models = [ModelConfiguration(id=str(uuid.uuid4()), platform_id=platform.id,
                  model_name=name, enabled=not requires_thinking(name)) for name in data.model_names]
        session.add_all(models)
        await session.commit()
        await load_connections(session)
        return public_configuration(platform, models)


@router.put("/{configuration_id}")
async def update_configuration(configuration_id: str, data: ModelConfigurationInput,
                               user=Depends(require_model_admin), session: AsyncSession = Depends(get_session)):
    async with configuration_lock:
        platform = await get_configuration(session, configuration_id)
        api_key = api_key_from_input(data, platform)
        models = await get_models(session, platform.id)
        endpoint_changed = (platform.provider, platform.base_url) != (data.provider, data.base_url)
        removed = [row for row in models if row.model_name not in data.model_names]
        for row in models if endpoint_changed else removed:
            if await configuration_in_use(session, row):
                raise HTTPException(409, "该模型仍在未结束的对局中使用，请待对局结束后删除模型或修改平台、地址")
        for row in removed:
            await session.delete(row)
        by_name = {row.model_name: row for row in models if row not in removed}
        updated = []
        for name in data.model_names:
            row = by_name.get(name)
            if row is None:
                row = ModelConfiguration(id=str(uuid.uuid4()), platform_id=platform.id, model_name=name)
                session.add(row)
            row.enabled = not requires_thinking(name)
            updated.append(row)
        platform.provider, platform.base_url, platform.api_key = data.provider, data.base_url, api_key
        platform.enabled, platform.thinking_format = data.enabled, data.thinking_format
        await session.commit()
        await load_connections(session)
        return public_configuration(platform, updated)


@router.delete("/{configuration_id}")
async def delete_configuration(configuration_id: str, user=Depends(require_model_admin),
                               session: AsyncSession = Depends(get_session)):
    async with configuration_lock:
        platform = await get_configuration(session, configuration_id)
        models = await get_models(session, platform.id)
        for row in models:
            if await configuration_in_use(session, row):
                raise HTTPException(409, "该模型仍在未结束的对局中使用，可先停用，待对局结束后删除")
        for row in models:
            await session.delete(row)
        await session.flush()
        await session.delete(platform)
        await session.commit()
        await load_connections(session)
        return {"ok": True}


class ModelTestInput(ModelConfigurationInput):
    id: Optional[str] = None
    model_name: Optional[str] = None


@router.post("/test")
async def test_configuration(data: ModelTestInput, request: Request, user=Depends(require_model_admin),
                             session: AsyncSession = Depends(get_session)):
    if getattr(request.app.state, "ai_mode", "live") == "simulated":
        raise HTTPException(409, "当前是模拟预览服务，请在真实服务中测试模型连通性")
    existing = await get_configuration(session, data.id) if data.id else None
    name = data.model_name or data.model_names[0]
    if name not in data.model_names:
        raise HTTPException(422, "请选择当前配置中的模型进行测试")
    connection = ModelConnection(id=str(uuid.uuid4()), provider=data.provider, model_name=name,
        api_key=api_key_from_input(data, existing), base_url=data.base_url,
        thinking_format=data.thinking_format)
    try:
        extra_body_for(name, connection.provider, thinking_format=connection.thinking_format)
    except ValueError as error:
        raise HTTPException(422, str(error))
    started = time.monotonic()
    client = None
    success = False
    error_code = None
    message = "连接成功，模型已返回有效 JSON。"
    try:
        client = LLMClient(connection=connection)
        result = await asyncio.wait_for(client.chat(
            "你正在执行连接测试。仅返回 JSON 对象，不输出解释。",
            '请回复 {"ok": true}。', json_mode=True,
        ), timeout=20)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ValueError("invalid_test_response")
        success = True
    except (asyncio.TimeoutError, APITimeoutError):
        error_code, message = "timeout", "连接超时（20 秒），请检查服务地址或稍后重试。"
    except APIConnectionError:
        error_code, message = "connection_error", "无法连接服务，请检查 Base URL 和网络。"
    except APIStatusError as error:
        error_code = "http_{0}".format(error.status_code)
        message = {
            401: "API Key 无效或已过期。", 403: "没有调用权限，请检查 API Key 和模型开通状态。",
            404: "模型或接口不存在，请检查模型名和 Base URL。",
            429: "调用限流或额度不足，请检查账户额度后重试。",
            400: "服务拒绝了参数，请检查模型名、关闭 Thinking 参数及 JSON 输出支持。",
            422: "服务拒绝了参数，请检查 关闭 Thinking 参数格式及 JSON 输出支持。",
        }.get(error.status_code, "模型服务返回错误（HTTP {0}），请稍后重试。".format(error.status_code))
    except ValueError:
        error_code, message = "invalid_response", "模型未返回有效测试 JSON，或不支持当前 关闭 Thinking 参数。"
    except Exception:
        error_code, message = "service_error", "模型调用失败，请检查配置后重试。"
    finally:
        if client is not None:
            await client.client.close()
    return {"success": success, "model_name": connection.model_name, "message": message,
            "error_code": error_code, "elapsed_ms": round((time.monotonic() - started) * 1000)}
