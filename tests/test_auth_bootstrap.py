"""Exercise real startup and upgrades outside the unit suite's DB stub."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def run_startup_check(tmp_path, program, extra_env=None):
    root = Path(__file__).resolve().parents[1]
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("WW_") and key != "PYTHONPATH"}
    environment.update({
        "PYTHONPATH": str(root),
        "WW_DB_URL": "sqlite+aiosqlite:///" + str(tmp_path / "accounts.db"),
        "WW_LLM_AUDIT_PATH": "",
    })
    if extra_env:
        environment.update(extra_env)
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(program)],
                            cwd=str(tmp_path), env=environment,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_fresh_database_admin_login_permissions_and_password_preservation(tmp_path):
    run_startup_check(tmp_path, '''
        import asyncio
        import bcrypt
        import httpx
        from sqlalchemy import select
        from db.session import async_session, init_db
        from main import app
        from models.user import User

        async def check():
            await init_db(import_legacy=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
                assert response.status_code == 200
                assert response.json()["is_admin"] is True
                headers = {"Authorization": "Bearer " + response.json()["token"]}
                assert (await client.get("/api/auth/me", headers=headers)).json()["is_admin"] is True
                assert (await client.get("/api/model-configurations", headers=headers)).status_code == 200

                # A chosen username does not grant administrator authority.
                created = await client.post("/api/admin/create-user", headers=headers, json={"username": "neighbor"})
                assert created.status_code == 200
                login = await client.post("/api/auth/login", json=created.json())
                assert login.status_code == 200 and login.json()["is_admin"] is False
                ordinary = {"Authorization": "Bearer " + login.json()["token"]}
                assert (await client.get("/api/model-configurations", headers=ordinary)).status_code == 403
                assert (await client.post("/api/admin/create-user", headers=ordinary, json={"username": "another"})).status_code == 403

                async with async_session() as session:
                    users = (await session.execute(select(User))).scalars().all()
                    assert sum(user.is_admin for user in users) == 1
                    assert all(not user.is_admin for user in users if user.is_ai)
                    administrator = next(user for user in users if user.username == "admin")
                    administrator.password_hash = bcrypt.hashpw(b"chosen-password", bcrypt.gensalt()).decode()
                    await session.commit()
                await init_db(import_legacy=False)
                assert (await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})).status_code == 401
                changed = await client.post("/api/auth/login", json={"username": "admin", "password": "chosen-password"})
                assert changed.status_code == 200 and changed.json()["is_admin"] is True

        asyncio.run(check())
    ''')


def test_fresh_database_uses_configured_admin_credentials(tmp_path):
    run_startup_check(tmp_path, '''
        import asyncio
        import httpx
        from main import app
        from db.session import init_db

        async def check():
            await init_db(import_legacy=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                denied = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
                assert denied.status_code == 401
                response = await client.post("/api/auth/login", json={"username": "keeper", "password": "local-secret"})
                assert response.status_code == 200 and response.json()["is_admin"] is True

        asyncio.run(check())
    ''', {"WW_ADMIN_USERNAME": "keeper", "WW_ADMIN_PASSWORD": "local-secret"})


def test_blank_admin_settings_fall_back_to_defaults(tmp_path):
    run_startup_check(tmp_path, '''
        import asyncio
        import httpx
        from main import app
        from db.session import init_db

        async def check():
            await init_db(import_legacy=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
                assert response.status_code == 200 and response.json()["is_admin"] is True

        asyncio.run(check())
    ''', {"WW_ADMIN_USERNAME": "  ", "WW_ADMIN_PASSWORD": ""})


def test_legacy_upgrade_keeps_original_admin_without_promoting_named_users(tmp_path):
    run_startup_check(tmp_path, '''
        import asyncio
        import bcrypt
        import httpx
        import sqlite3
        from pathlib import Path
        from sqlalchemy import select
        from db.session import async_session, init_db
        from main import app
        from models.user import User

        password_hash = bcrypt.hashpw(b"existing-password", bcrypt.gensalt()).decode()
        connection = sqlite3.connect(Path("accounts.db"))
        connection.execute("""CREATE TABLE user (
            id INTEGER PRIMARY KEY, username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL, nickname VARCHAR(50) NOT NULL,
            is_ai BOOLEAN NOT NULL DEFAULT 0, model_name VARCHAR(100),
            experience TEXT, created_at DATETIME
        )""")
        connection.executemany("INSERT INTO user (id, username, password_hash, nickname, is_ai) VALUES (?, ?, ?, ?, ?)", [
            (1, "original-owner", password_hash, "original-owner", False),
            (2, "admin", password_hash, "ordinary-player", False),
            (3, "ai-player", password_hash, "AI", True),
        ])
        connection.commit()
        connection.close()

        async def check():
            await init_db(import_legacy=False)
            async with async_session() as session:
                users = (await session.execute(select(User).order_by(User.id))).scalars().all()
                assert len(users) == 3
                assert [user.is_admin for user in users] == [True, False, False]
                assert all(user.password_hash == password_hash for user in users)
                assert users[0].nickname == "original-owner"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                owner = await client.post("/api/auth/login", json={"username": "original-owner", "password": "existing-password"})
                assert owner.status_code == 200 and owner.json()["is_admin"] is True
                headers = {"Authorization": "Bearer " + owner.json()["token"]}
                assert (await client.get("/api/model-configurations", headers=headers)).status_code == 200
                ordinary = await client.post("/api/auth/login", json={"username": "admin", "password": "existing-password"})
                assert ordinary.status_code == 200 and ordinary.json()["is_admin"] is False
                ordinary_headers = {"Authorization": "Bearer " + ordinary.json()["token"]}
                assert (await client.get("/api/model-configurations", headers=ordinary_headers)).status_code == 403
                assert (await client.post("/api/admin/create-user", headers=ordinary_headers, json={"username": "another"})).status_code == 403

            # Later starts must not grant a role that has been revoked.
            async with async_session() as session:
                owner = await session.get(User, 1)
                owner.is_admin = False
                await session.commit()
            await init_db(import_legacy=False)
            async with async_session() as session:
                assert (await session.get(User, 1)).is_admin is False

        asyncio.run(check())
    ''', {"WW_ADMIN_USERNAME": "original-owner"})
