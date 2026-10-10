import os

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from sqlalchemy.types import BigInteger
from config import settings


@compiles(BigInteger, "sqlite")
def _compile_big_int_sqlite(element, compiler, **kw):
    # SQLite only autoincrements a primary key declared as INTEGER.
    return "INTEGER"


def _create_engine():
    url = settings.database_url
    if url.startswith("sqlite"):
        path = url.split("///", 1)[-1]
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        eng = create_async_engine(url, echo=False, poolclass=NullPool)

        @event.listens_for(eng.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        return eng
    return create_async_engine(url, echo=False, pool_size=10, pool_recycle=3600)


engine = _create_engine()
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db(import_legacy: bool = True):
    from models.game import Game, GamePlayer, WitchState  # noqa
    from models.event import GameEvent  # noqa
    from models.memory import PlayerMemory  # noqa
    from models.user import User, UserSession  # noqa
    from models.game_result import GameResult  # noqa
    from models.model_configuration import ModelConfiguration, ModelPlatformConfiguration  # noqa
    from ai.model_configuration import load_connections, migrate_legacy_connections, migrate_platform_connections

    async with engine.begin() as conn:
        had_model_configuration = await conn.run_sync(
            lambda connection: inspect(connection).has_table("model_configuration")
        )
        await conn.run_sync(Base.metadata.create_all)
        from db.migrations import ensure_user_admin_role
        await conn.run_sync(ensure_user_admin_role)
        if settings.database_url.startswith("sqlite"):
            await conn.run_sync(_ensure_sqlite_columns)

    # AI user identities stay stable; model assignment comes from page settings.
    await _seed_users()
    async with async_session() as session:
        if not had_model_configuration and import_legacy:
            await migrate_legacy_connections(session)
        await migrate_platform_connections(session)
        await load_connections(session)


def _ensure_sqlite_columns(sync_conn):
    """create_all does not ALTER existing SQLite tables."""
    rows = sync_conn.exec_driver_sql("PRAGMA table_info(game_player)").fetchall()
    columns = {row[1] for row in rows}
    if "model_name" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE game_player ADD COLUMN model_name VARCHAR(100)"
        )
    if "can_vote" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE game_player ADD COLUMN can_vote BOOLEAN NOT NULL DEFAULT 1"
        )
    if "model_config_id" not in columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE game_player ADD COLUMN model_config_id VARCHAR(36)"
        )
    model_columns = {row[1] for row in sync_conn.exec_driver_sql(
        "PRAGMA table_info(model_configuration)").fetchall()}
    if "platform_id" not in model_columns:
        sync_conn.exec_driver_sql(
            "ALTER TABLE model_configuration ADD COLUMN platform_id VARCHAR(36) "
            "REFERENCES model_platform_configuration(id)"
        )


async def _seed_users():
    from sqlalchemy import select
    from models.user import User
    import bcrypt as _bcrypt
    from engine.nicknames import NICKNAME_POOL

    async with async_session() as session:
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none() is not None:
            return  # Already seeded

        def _hash_pw(pw: str) -> str:
            return _bcrypt.hashpw(pw.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")

        # Create human user. Credentials come from WW_ADMIN_USERNAME / WW_ADMIN_PASSWORD.
        human = User(
            username=settings.admin_username,
            password_hash=_hash_pw(settings.admin_password),
            nickname=settings.admin_username,
            is_ai=False,
            is_admin=True,
        )
        session.add(human)

        # Models belong to seats, not recurring AI identities.
        for i, nick in enumerate(NICKNAME_POOL):
            ai_user = User(
                username=f"ai_{nick}",
                password_hash=_hash_pw("not_a_real_password"),
                nickname=nick,
                is_ai=True,
            )
            session.add(ai_user)

        await session.commit()


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
