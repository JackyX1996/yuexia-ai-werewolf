"""Database persistence, request routing, secret handling and connectivity probes."""
import random
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from openai import APIStatusError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from ai.llm_client import LLMClient, get_llm_client
from ai.model_configuration import (
    ModelConnection, ModelConfigurationError, PROVIDER_URLS, load_connections,
    migrate_legacy_connections, migrate_platform_connections, pick_connections, replace_connections, resolve_connection,
)
from ai.model_pool import extra_body_for
from api.auth import get_current_user
from api.model_configuration import router
from db.session import Base, get_session
from engine.game_manager import GameManager
from main import validation_error
from models.game import Game, GamePlayer, GameStatus, Role
from models.model_configuration import ModelConfiguration, ModelPlatformConfiguration
from models.user import User
from schemas.model_configuration import ModelConfigurationInput


def connection(identifier="one", provider="bailian", name="my-custom-model", **kwargs):
    return ModelConnection(id=identifier, provider=provider, model_name=name,
                           api_key=kwargs.pop("api_key", "private-test-key"),
                           base_url=kwargs.pop("base_url", PROVIDER_URLS.get(provider, "http://localhost:1234/v1")),
                           **kwargs)


@pytest_asyncio.fixture
async def model_api():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as database:
        await database.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.state.ai_mode = "live"
    app.include_router(router, prefix="/api")
    app.add_exception_handler(RequestValidationError, validation_error)
    current_user = SimpleNamespace(username="admin", id=1, is_admin=True, is_ai=False)

    async def session_dependency():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_session] = session_dependency
    replace_connections([])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions, current_user, app
    await engine.dispose()


def payload(provider="bailian", **kwargs):
    name = kwargs.pop("model_name", "custom-model")
    values = {"provider": provider, "model_names": [name], "api_key": "private-test-key"}
    if provider == "openai":
        values["base_url"] = "http://localhost:1234/v1"
    values.update(kwargs)
    return values


@pytest.mark.parametrize("provider,flags", [
    ("bailian", {"enable_thinking": False}),
    ("volcengine", {"thinking": {"type": "disabled"}}),
    ("openai", {"thinking": {"type": "disabled"}}),
])
def test_defaults_disable_thinking_and_use_explicit_provider(provider, flags):
    saved = connection(provider=provider, name="doubao-custom-on-another-platform")
    assert saved.thinking_enabled is False
    assert extra_body_for(saved.model_name, saved.provider) == flags
    assert "private-test-key" not in repr(saved)


@pytest.mark.parametrize("thinking_format,flags", [
    ("enable_thinking", {"enable_thinking": False}),
    ("thinking", {"thinking": {"type": "disabled"}}),
    ("reasoning_effort", {"reasoning_effort": "none"}),
    ("none", {}),
])
def test_compatible_thinking_formats(thinking_format, flags):
    assert extra_body_for("custom", "openai", thinking_format=thinking_format) == flags


def test_sampling_only_uses_enabled_connections_and_repeats_small_pool():
    pool = [connection("a"), connection("b", name="other"), connection("c", enabled=False)]
    replace_connections(pool)
    picked = pick_connections(11, random.Random(4))
    assert len(picked) == 11
    assert {item.id for item in picked} == {"a", "b"}
    assert len({item.id for item in picked[:2]}) == 2
    replace_connections([])
    with pytest.raises(ModelConfigurationError, match="管理员"):
        pick_connections(11)


def test_a_single_available_model_can_fill_all_eleven_ai_seats():
    saved = connection("only-model")
    replace_connections([saved, connection("disabled", enabled=False), connection("no-key", api_key="")])
    assert pick_connections(11, random.Random(1)) == [saved] * 11


def test_large_model_pool_samples_eleven_distinct_configs_from_the_entire_pool():
    pool = [connection("model-{0}".format(index), name="custom-{0}".format(index)) for index in range(500)]
    replace_connections(pool)
    picked = pick_connections(11, random.Random(1))
    assert len(picked) == len({item.id for item in picked}) == 11
    assert {item.id for item in picked}.issubset({item.id for item in pool})
    assert any(int(item.id.split("-")[1]) >= 100 for item in picked)


def test_same_name_routes_by_configuration_id_and_rejects_ambiguous_legacy_name():
    first, second = connection("a"), connection("b", provider="volcengine")
    replace_connections([first, second])
    assert resolve_connection("a") == first
    assert resolve_connection("b") == second
    with pytest.raises(ModelConfigurationError, match="同名"):
        resolve_connection(first.model_name)


def test_saved_changes_replace_cached_client_without_restart():
    first = connection()
    replace_connections([first])
    original = get_llm_client("one")
    assert get_llm_client("one") is original
    changed = replace(first, api_key="updated-key", model_name="updated-name")
    replace_connections([changed])
    updated = get_llm_client("one")
    assert updated is not original
    assert updated.model == "updated-name"
    assert updated.client.api_key == "updated-key"
    assert updated.extra_body == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_completion_sends_configured_endpoint_model_and_thinking_flags():
    saved = connection(provider="volcengine", name="arbitrary-custom-id")
    client = LLMClient(connection=saved)
    captured = []

    def respond(request):
        import json
        captured.append((str(request.url), request.headers["authorization"], json.loads(request.content)))
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
            "model": saved.model_name, "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"ok":true}'}}]})

    await client.client.close()
    client.client = __import__("openai").AsyncOpenAI(
        api_key=saved.api_key, base_url=saved.base_url,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    try:
        assert await client.chat("system", "JSON") == {"ok": True}
    finally:
        await client.client.close()
    url, authorization, sent = captured[0]
    assert url == PROVIDER_URLS["volcengine"] + "/chat/completions"
    assert authorization == "Bearer private-test-key"
    assert sent["model"] == "arbitrary-custom-id"
    assert sent["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_crud_persists_without_returning_keys_and_preserves_blank_key(model_api):
    client, sessions, _, _ = model_api
    created = await client.post("/api/model-configurations", json=payload())
    assert created.status_code == 200
    assert "private-test-key" not in created.text
    saved = created.json()
    assert saved["has_api_key"] and not saved["thinking_enabled"]
    assert saved["base_url"] == PROVIDER_URLS["bailian"]
    configuration_id = saved["id"]
    updated = await client.put("/api/model-configurations/" + configuration_id,
                               json=payload(api_key=None, model_name="new-model"))
    assert updated.status_code == 200
    model_id = updated.json()["models"][0]["id"]
    assert resolve_connection(model_id).model_name == "new-model"
    assert resolve_connection(model_id).api_key == "private-test-key"
    replace_connections([])
    async with sessions() as session:
        await load_connections(session)
    assert resolve_connection(model_id).model_name == "new-model"
    listed = await client.get("/api/model-configurations")
    assert "api_key" not in listed.json()[0]
    assert "private-test-key" not in listed.text
    assert (await client.delete("/api/model-configurations/" + configuration_id)).status_code == 200
    assert (await client.get("/api/model-configurations")).json() == []
    with pytest.raises(ModelConfigurationError):
        resolve_connection()


@pytest.mark.asyncio
async def test_lobby_readiness_tracks_empty_single_disabled_and_deleted_models(model_api):
    client, _, _, _ = model_api

    async def status():
        response = await client.get("/api/model-configurations/status")
        assert response.status_code == 200
        return response.json()

    assert await status() == {"available_count": 0, "can_start": False, "ai_mode": "live"}
    saved = (await client.post("/api/model-configurations", json=payload())).json()
    assert await status() == {"available_count": 1, "can_start": True, "ai_mode": "live"}
    await client.put("/api/model-configurations/" + saved["id"], json=payload(api_key=None, enabled=False))
    assert (await status())["can_start"] is False
    await client.put("/api/model-configurations/" + saved["id"], json=payload(api_key=None, enabled=True))
    assert (await status())["can_start"] is True
    await client.delete("/api/model-configurations/" + saved["id"])
    assert (await status())["available_count"] == 0


@pytest.mark.asyncio
async def test_ordinary_players_can_check_readiness_without_seeing_connections(model_api):
    client, _, user, _ = model_api
    await client.post("/api/model-configurations", json=payload())
    user.username = "ordinary-player"
    user.is_admin = False
    response = await client.get("/api/model-configurations/status")
    assert response.status_code == 200
    assert response.json() == {"available_count": 1, "can_start": True, "ai_mode": "live"}
    assert "private-test-key" not in response.text and "custom-model" not in response.text
    assert (await client.get("/api/model-configurations")).status_code == 403


@pytest.mark.asyncio
async def test_simulated_preview_lobby_can_start_without_real_model_configuration(model_api):
    client, _, _, app = model_api
    app.state.ai_mode = "simulated"
    response = await client.get("/api/model-configurations/status")
    assert response.json() == {"available_count": 0, "can_start": True, "ai_mode": "simulated"}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["bailian", "volcengine", "openai"])
async def test_all_modes_require_keys_and_custom_models_are_allowed(model_api, provider):
    client, _, _, _ = model_api
    assert (await client.post("/api/model-configurations", json=payload(provider, api_key=""))).status_code == 422
    assert (await client.post("/api/model-configurations", json=payload(provider, model_name="my/arbitrary-model"))).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_url", ["", "file:///etc/passwd", "https://user:pass@test/v1", "https://test/v1?key=value", "https://test/v1/chat/completions", "http://test:invalid/v1"])
async def test_url_validation_does_not_echo_secret(model_api, bad_url):
    client, _, _, _ = model_api
    response = await client.post("/api/model-configurations", json=payload("openai", base_url=bad_url))
    assert response.status_code == 422
    assert "private-test-key" not in response.text


@pytest.mark.asyncio
async def test_changed_provider_or_endpoint_requires_reentering_key(model_api):
    client, _, _, _ = model_api
    saved = (await client.post("/api/model-configurations", json=payload())).json()
    response = await client.put("/api/model-configurations/" + saved["id"], json=payload("openai", api_key=None))
    assert response.status_code == 422
    assert resolve_connection(saved["models"][0]["id"]).provider == "bailian"


@pytest.mark.asyncio
async def test_configuration_management_is_admin_only(model_api):
    client, _, user, _ = model_api
    user.username = "ordinary-player"
    user.is_admin = False
    for method, url, body in [("GET", "", None), ("POST", "", payload()),
                              ("PUT", "/unknown", payload()), ("DELETE", "/unknown", None),
                              ("POST", "/test", payload())]:
        response = await client.request(method, "/api/model-configurations" + url, json=body)
        assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("username", ["admin", "former-owner"])
async def test_administrator_names_do_not_grant_configuration_access(model_api, username):
    client, _, user, _ = model_api
    user.username = username
    user.is_admin = False
    assert (await client.get("/api/model-configurations")).status_code == 403


@pytest.mark.asyncio
async def test_administrator_role_works_with_a_different_username(model_api):
    client, _, user, _ = model_api
    user.username = "renamed-administrator"
    assert (await client.get("/api/model-configurations")).status_code == 200
    user.is_ai = True
    assert (await client.get("/api/model-configurations")).status_code == 403


@pytest.mark.asyncio
async def test_anonymous_users_cannot_access_configuration_or_probe(model_api):
    client, _, _, app = model_api
    del app.dependency_overrides[get_current_user]
    assert (await client.get("/api/model-configurations")).status_code == 401
    assert (await client.get("/api/model-configurations/status")).status_code == 401
    assert (await client.post("/api/model-configurations/test", json=payload())).status_code == 401


@pytest.mark.asyncio
async def test_thinking_is_fixed_off_and_thinking_only_models_stay_unavailable(model_api):
    client, _, _, _ = model_api
    response = await client.post("/api/model-configurations", json=payload(model_name="glm-5.3"))
    assert response.status_code == 200
    assert not response.json()["models"][0]["enabled"]
    assert (await client.get("/api/model-configurations/status")).json()["can_start"] is False
    assert (await client.post("/api/model-configurations", json=payload(model_name="glm-5.3", thinking_enabled=True))).status_code == 422
    assert (await client.post("/api/model-configurations/test", json=payload(model_name="glm-5.3"))).status_code == 422


@pytest.mark.parametrize("name", ["MiniMax-M2.1", "MiniMax/MiniMax-M2.5", "MiniMax/MiniMax-M2.7", "kimi/kimi-k3"])
def test_compatible_thinking_only_models_never_silently_enable_thinking(name):
    with pytest.raises(ValueError, match="不支持关闭 Thinking"):
        extra_body_for(name, "openai")
    with pytest.raises(ValueError, match="固定关闭"):
        extra_body_for(name, "openai", thinking_enabled=True)


@pytest.mark.asyncio
async def test_connectivity_probe_uses_unsaved_input_and_never_persists_it(model_api, monkeypatch):
    client, _, _, _ = model_api
    fake = SimpleNamespace(chat=AsyncMock(return_value={"ok": True}), client=SimpleNamespace(close=AsyncMock()))
    constructor = MagicMock(return_value=fake)
    monkeypatch.setattr("api.model_configuration.LLMClient", constructor)
    response = await client.post("/api/model-configurations/test", json=payload("openai", model_name="unsaved-model"))
    assert response.json()["success"]
    sent = constructor.call_args.kwargs["connection"]
    assert sent.model_name == "unsaved-model" and not sent.thinking_enabled
    assert sent.base_url == "http://localhost:1234/v1"
    fake.chat.assert_awaited_once()
    fake.client.close.assert_awaited_once()
    assert (await client.get("/api/model-configurations")).json() == []


@pytest.mark.asyncio
async def test_probe_can_use_saved_key_while_testing_new_model_name(model_api, monkeypatch):
    client, _, _, _ = model_api
    saved = (await client.post("/api/model-configurations", json=payload())).json()
    constructor = MagicMock(return_value=SimpleNamespace(chat=AsyncMock(return_value={"ok": True}),
        client=SimpleNamespace(close=AsyncMock())))
    monkeypatch.setattr("api.model_configuration.LLMClient", constructor)
    response = await client.post("/api/model-configurations/test", json=payload(id=saved["id"], api_key=None, model_name="new-model"))
    assert response.json()["success"]
    assert constructor.call_args.kwargs["connection"].api_key == "private-test-key"
    assert resolve_connection(saved["models"][0]["id"]).model_name == "custom-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,code", [(401, "http_401"), (404, "http_404"), (429, "http_429"), ("timeout", "timeout"), ("invalid", "invalid_response")])
async def test_probe_errors_are_useful_and_never_echo_provider_payload(model_api, monkeypatch, failure, code):
    client, _, _, _ = model_api
    if isinstance(failure, int):
        exception = APIStatusError("provider echoed private-test-key", response=httpx.Response(failure,
            request=httpx.Request("POST", "http://provider/v1")), body={"secret": "private-test-key"})
    else:
        exception = TimeoutError() if failure == "timeout" else ValueError("private-test-key")
    fake = SimpleNamespace(chat=AsyncMock(side_effect=exception), client=SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr("api.model_configuration.LLMClient", lambda **kwargs: fake)
    response = await client.post("/api/model-configurations/test", json=payload())
    assert not response.json()["success"]
    assert response.json()["error_code"] == code
    assert "private-test-key" not in response.text
    fake.client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_service_does_not_send_real_probe(model_api, monkeypatch):
    client, _, _, app = model_api
    app.state.ai_mode = "simulated"
    constructor = MagicMock()
    monkeypatch.setattr("api.model_configuration.LLMClient", constructor)
    assert (await client.post("/api/model-configurations/test", json=payload())).status_code == 409
    constructor.assert_not_called()


@pytest.mark.asyncio
async def test_models_in_unfinished_games_cannot_be_deleted(model_api):
    client, sessions, _, _ = model_api
    saved = (await client.post("/api/model-configurations", json=payload())).json()
    async with sessions() as session:
        session.add(User(id=1, username="human", nickname="human", password_hash="unused", is_ai=False))
        session.add(Game(id=1, status=GameStatus.running))
        await session.flush()
        session.add(GamePlayer(id=1, game_id=1, seat=1, role=Role.villager,
                               model_name=saved["model_names"][0], model_config_id=saved["models"][0]["id"]))
        await session.commit()
    assert (await client.delete("/api/model-configurations/" + saved["id"])).status_code == 409
    assert (await client.put("/api/model-configurations/" + saved["id"],
                            json=payload(model_name="a-different-model"))).status_code == 409
    assert (await client.put("/api/model-configurations/" + saved["id"],
                            json=payload(api_key=None, enabled=False))).status_code == 200
    async with sessions() as session:
        game = await session.get(Game, 1)
        game.status = GameStatus.finished
        await session.commit()
    assert (await client.delete("/api/model-configurations/" + saved["id"])).status_code == 200


@pytest.mark.asyncio
async def test_settlement_records_model_names_instead_of_connection_ids():
    gm = GameManager(AsyncMock())
    gm.repo = AsyncMock()
    gm._update_ai_experiences = AsyncMock()
    player = SimpleNamespace(seat=1, role=Role.villager, is_human=False,
                             is_alive=True, user_id=None, nickname="AI",
                             model_name="human-readable-model", model_config_id="connection-uuid")
    gm.repo.get_players.return_value = [player]
    gm.repo.get_all_events.return_value = []
    await gm._end_game(SimpleNamespace(id=1, current_round=1), "good")
    results = gm.repo.save_game_results.call_args.args[0]
    assert results[0].model_name == "human-readable-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("name,thinking_format,flags", [
    ("gpt-4o", "auto", {}),
    ("gpt-5.1", "auto", {"reasoning_effort": "none"}),
    ("MiniMax-M3", "auto", {"thinking": {"type": "disabled"}}),
])
async def test_compatible_openai_models_have_appropriate_thinking_and_token_parameters(name, thinking_format, flags):
    saved = connection(provider="openai", name=name, thinking_format=thinking_format)
    client = LLMClient(connection=saved)
    await client.client.close()
    client.client = AsyncMock()
    client.client.chat.completions.create.return_value = SimpleNamespace(choices=[
        SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"ok":true}'))])
    assert await client.chat("system", "JSON") == {"ok": True}
    sent = client.client.chat.completions.create.call_args.kwargs
    assert sent.get("extra_body", {}) == flags
    if name == "gpt-5.1":
        assert "max_completion_tokens" in sent
        assert "max_tokens" not in sent and "temperature" not in sent


@pytest.mark.asyncio
async def test_game_creation_binds_configuration_ids_even_with_identical_names():
    first, second = connection("first"), connection("second", provider="volcengine")
    replace_connections([first, second])
    gm = GameManager(AsyncMock())
    gm.repo = AsyncMock()
    gm.repo.create_game.return_value = SimpleNamespace(id=123)
    gm.repo.get_ai_users.return_value = []
    await gm.create_game(human_user_id=1)
    ai_seats = [call.kwargs for call in gm.repo.add_player.call_args_list if not call.kwargs["is_human"]]
    assert len(ai_seats) == 11
    assert {seat["model_config_id"] for seat in ai_seats} == {"first", "second"}
    assert {seat["model_name"] for seat in ai_seats} == {first.model_name}
    assert await gm._get_player_model(SimpleNamespace(is_human=False, model_config_id="second")) == "second"
    replace_connections([])
    gm.repo.create_game.reset_mock()
    with pytest.raises(ModelConfigurationError):
        await gm.create_game(human_user_id=1)
    gm.repo.create_game.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_import_defaults_to_thinking_off_and_keeps_custom_endpoint(model_api, monkeypatch):
    _, sessions, _, _ = model_api
    monkeypatch.setattr("dotenv.dotenv_values", lambda path: {
        "WW_DASHSCOPE_API_KEY": "legacy-test-key", "WW_VOLCENGINE_API_KEY": "",
        "WW_DASHSCOPE_BASE_URL": "https://legacy.example/v1", "WW_LLM_MODEL": "custom/legacy-name",
    })
    for name in ("WW_DASHSCOPE_API_KEY", "WW_VOLCENGINE_API_KEY", "WW_DASHSCOPE_BASE_URL",
                 "WW_DASHSCOPE_WORKSPACE_ID", "WW_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    async with sessions() as session:
        await migrate_legacy_connections(session)
        await load_connections(session)
    saved = resolve_connection("custom/legacy-name")
    assert saved.provider == "openai" and saved.base_url == "https://legacy.example/v1"
    assert saved.api_key == "legacy-test-key"
    assert not saved.thinking_enabled
    thinking_only = resolve_connection("glm-5.3")
    assert not thinking_only.enabled and not thinking_only.thinking_enabled


@pytest.mark.asyncio
async def test_platform_shares_credentials_across_unlimited_models_and_preserves_ids(model_api):
    client, sessions, _, _ = model_api
    names = ['custom-{0}'.format(index) for index in range(500)]
    saved = (await client.post('/api/model-configurations', json=payload(
        'openai', model_names=names + [names[0]]))).json()
    assert len(saved['models']) == 500
    assert (await client.get('/api/model-configurations/status')).json()['available_count'] == 500
    ids = {row['model_name']: row['id'] for row in saved['models']}
    picked = pick_connections(11, random.Random(1))
    assert len({item.id for item in picked}) == 11
    assert {item.api_key for item in picked} == {'private-test-key'}
    assert {item.base_url for item in picked} == {'http://localhost:1234/v1'}
    async with sessions() as session:
        from sqlalchemy import select
        platforms = (await session.execute(select(ModelPlatformConfiguration))).scalars().all()
        rows = (await session.execute(select(ModelConfiguration))).scalars().all()
        assert len(platforms) == 1 and platforms[0].api_key == 'private-test-key'
        assert all(row.api_key == '' and row.platform_id == saved['id'] for row in rows)
    changed = await client.put('/api/model-configurations/' + saved['id'], json=payload(
        'openai', model_names=names + ['new-model'], api_key='rotated-key'))
    assert changed.status_code == 200
    assert {row['model_name']: row['id'] for row in changed.json()['models'] if row['model_name'] in ids} == ids
    assert all(resolve_connection(identifier).api_key == 'rotated-key' for identifier in ids.values())


@pytest.mark.asyncio
async def test_probe_selects_one_model_from_the_shared_platform(model_api, monkeypatch):
    client, _, _, _ = model_api
    saved = (await client.post('/api/model-configurations', json=payload(
        'openai', model_names=['first', 'second']))).json()
    constructor = MagicMock(return_value=SimpleNamespace(chat=AsyncMock(return_value={'ok': True}),
        client=SimpleNamespace(close=AsyncMock())))
    monkeypatch.setattr('api.model_configuration.LLMClient', constructor)
    body = payload('openai', model_names=['first', 'second'], api_key=None, id=saved['id'])
    response = await client.post('/api/model-configurations/test', json={**body, 'model_name': 'second'})
    assert response.json()['success'] and response.json()['model_name'] == 'second'
    assert constructor.call_args.kwargs['connection'].api_key == 'private-test-key'
    response = await client.post('/api/model-configurations/test', json={**body, 'model_name': 'missing'})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_in_use_platform_can_add_models_but_cannot_remove_bound_model_or_change_endpoint(model_api):
    client, sessions, _, _ = model_api
    saved = (await client.post('/api/model-configurations', json=payload(
        'openai', model_names=['bound-model', 'unused-model']))).json()
    bound = next(model for model in saved['models'] if model['model_name'] == 'bound-model')
    async with sessions() as session:
        session.add(Game(id=1, status=GameStatus.running))
        await session.flush()
        session.add(GamePlayer(id=1, game_id=1, seat=1, role=Role.werewolf,
            model_name=bound['model_name'], model_config_id=bound['id']))
        await session.commit()
    update = payload('openai', model_names=['bound-model', 'added-model'], api_key=None)
    response = await client.put('/api/model-configurations/' + saved['id'], json=update)
    assert response.status_code == 200
    assert next(row['id'] for row in response.json()['models'] if row['model_name'] == 'bound-model') == bound['id']
    assert (await client.put('/api/model-configurations/' + saved['id'], json=payload(
        'openai', model_names=['added-model'], api_key=None))).status_code == 409
    assert (await client.put('/api/model-configurations/' + saved['id'], json={
        **update, 'base_url': 'http://other.test/v1', 'api_key': 'new-key'})).status_code == 409


@pytest.mark.asyncio
async def test_upgrade_groups_old_credentials_preserves_seat_ids_and_forces_thinking_off(model_api):
    client, sessions, _, _ = model_api
    async with sessions() as session:
        for identifier, name, enabled, thinking in [('a', 'first', True, True),
            ('b', 'second', False, False), ('c', 'glm-5.3', True, True)]:
            session.add(ModelConfiguration(id=identifier, provider='bailian', model_name=name,
                api_key='old-key', base_url=PROVIDER_URLS['bailian'], enabled=enabled,
                thinking_enabled=thinking, thinking_format='auto'))
        session.add(ModelConfiguration(id='d', provider='bailian', model_name='different-key',
            api_key='another-key', base_url=PROVIDER_URLS['bailian']))
        await session.commit()
        await migrate_platform_connections(session)
        await migrate_platform_connections(session)  # Must be idempotent.
        await load_connections(session)
        assert (await session.get(ModelConfiguration, 'a')).api_key == ''
        assert (await session.get(ModelConfiguration, 'c')).thinking_enabled is False
    groups = (await client.get('/api/model-configurations')).json()
    assert len(groups) == 2
    assert len(next(group for group in groups if 'first' in group['model_names'])['models']) == 3
    assert resolve_connection('a').api_key == 'old-key' and not resolve_connection('a').thinking_enabled
    assert not resolve_connection('b').enabled and not resolve_connection('c').enabled
    assert (await client.get('/api/model-configurations/status')).json()['available_count'] == 2


@pytest.mark.parametrize('provider', ['bailian', 'volcengine', 'openai'])
def test_runtime_cannot_be_used_to_enable_thinking(provider):
    with pytest.raises(ValueError, match='固定关闭'):
        connection(provider=provider, thinking_enabled=True)
    with pytest.raises(ValueError, match='固定关闭'):
        extra_body_for('custom', provider, thinking_enabled=True)


@pytest.mark.asyncio
async def test_dead_wolf_identity_is_visible_only_to_wolf_teammates(model_api):
    from api.routes import _build_game_state
    _, sessions, _, _ = model_api
    async with sessions() as session:
        human = User(id=1, username='viewer', nickname='viewer', password_hash='unused', is_ai=False)
        session.add(human)
        session.add(Game(id=1, status=GameStatus.running))
        await session.flush()
        player = GamePlayer(id=1, game_id=1, user_id=1, seat=1, role=Role.werewolf,
            is_human=True, is_alive=True)
        session.add_all([player,
            GamePlayer(id=2, game_id=1, seat=2, role=Role.werewolf, is_alive=False),
            GamePlayer(id=3, game_id=1, seat=3, role=Role.villager, is_alive=False)])
        await session.commit()
        state = await _build_game_state(session, 1, human)
        assert state.players[1].role == 'werewolf' and not state.players[1].is_alive
        assert state.players[2].role is None
        player.role = Role.seer
        await session.commit()
        state = await _build_game_state(session, 1, human)
        assert state.players[1].role is None and state.players[2].role is None


def test_reasoning_model_cannot_omit_its_off_parameter():
    with pytest.raises(ValueError, match='明确关闭 Thinking'):
        ModelConfigurationInput(**payload('openai', model_name='gpt-5.1', thinking_format='none'))


@pytest.mark.asyncio
async def test_saved_key_is_masked_in_create_list_and_update_responses(model_api):
    client, _, _, _ = model_api
    secret = 'sk-abcdef0123456789uvwxyz'
    created = await client.post('/api/model-configurations', json=payload(api_key=secret))
    saved = created.json()
    assert saved['api_key_masked'] == 'sk-abc******wxyz'
    assert secret not in created.text and 'api_key' not in saved
    listed = await client.get('/api/model-configurations')
    assert listed.json()[0]['api_key_masked'] == saved['api_key_masked']
    assert secret not in listed.text
    updated = await client.put('/api/model-configurations/' + saved['id'], json=payload(api_key=None))
    assert updated.json()['api_key_masked'] == saved['api_key_masked']
    assert resolve_connection(updated.json()['models'][0]['id']).api_key == secret
    user = model_api[2]
    user.username = 'ordinary-player'
    user.is_admin = False
    assert (await client.get('/api/model-configurations')).status_code == 403
    assert 'api_key_masked' not in (await client.get('/api/model-configurations/status')).json()


@pytest.mark.parametrize('secret', ['', 'sk', 'abc123', '1234567890123456'])
def test_short_key_mask_never_returns_original_secret(secret):
    from api.model_configuration import masked_api_key
    assert masked_api_key(secret) == ('********' if secret else None)
