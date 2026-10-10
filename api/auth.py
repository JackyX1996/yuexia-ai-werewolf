import secrets
import string
from fastapi import APIRouter, Depends, HTTPException, Header
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session
from db.repository import GameRepository
from models.user import User

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user_id: int
    nickname: str
    username: str
    is_admin: bool


class UserInfo(BaseModel):
    user_id: int
    username: str
    nickname: str
    is_admin: bool


class CreateUserRequest(BaseModel):
    username: str


class CreateUserResponse(BaseModel):
    username: str
    password: str


async def get_current_user(
    authorization: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token = authorization[7:]
    repo = GameRepository(session)
    sess = await repo.get_session_by_token(token)
    if not sess:
        raise HTTPException(401, "登录已失效")
    user = await repo.get_user_by_id(sess.user_id)
    if not user:
        raise HTTPException(401, "用户不存在")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin or user.is_ai:
        raise HTTPException(403, "无权限")
    return user


@router.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest, session: AsyncSession = Depends(get_session)):
    import bcrypt as _bcrypt

    repo = GameRepository(session)
    user = await repo.get_user_by_username(req.username)
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    if not _bcrypt.checkpw(req.password.encode("utf-8"), user.password_hash.encode("utf-8")):
        raise HTTPException(401, "用户名或密码错误")

    token = secrets.token_hex(32)
    await repo.create_session(user.id, token)
    await session.commit()
    return LoginResponse(token=token, user_id=user.id, nickname=user.nickname,
                         username=user.username, is_admin=user.is_admin and not user.is_ai)


@router.post("/auth/logout")
async def logout(
    authorization: Optional[str] = Header(None),
    session: AsyncSession = Depends(get_session),
):
    if not authorization or not authorization.startswith("Bearer "):
        return {"ok": True}
    token = authorization[7:]
    repo = GameRepository(session)
    await repo.deactivate_session(token)
    await session.commit()
    return {"ok": True}


@router.get("/auth/me", response_model=UserInfo)
async def me(user: User = Depends(get_current_user)):
    return UserInfo(user_id=user.id, username=user.username, nickname=user.nickname,
                    is_admin=user.is_admin and not user.is_ai)


def _generate_password(length: int = 10) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%&*"
    return "".join(secrets.choice(chars) for _ in range(length))


@router.post("/admin/create-user", response_model=CreateUserResponse)
async def create_user(
    req: CreateUserRequest,
    user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    repo = GameRepository(session)
    existing = await repo.get_user_by_username(req.username)
    if existing:
        raise HTTPException(400, "用户名已存在")

    import bcrypt as _bcrypt
    password = _generate_password()
    password_hash = _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    await repo.create_user(
        username=req.username,
        password_hash=password_hash,
        nickname=req.username,
        is_ai=False,
    )
    await session.commit()
    return CreateUserResponse(username=req.username, password=password)


@router.get("/stats/by-user")
async def stats_by_user(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    repo = GameRepository(session)
    rows = await repo.get_stats_by_user(limit=10)
    result = []
    for r in rows:
        total = r["total"] or 0
        wins = r["wins"] or 0
        wolf_total = r["wolf_total"] or 0
        wolf_wins = r["wolf_wins"] or 0
        good_total = r["good_total"] or 0
        good_wins = r["good_wins"] or 0
        result.append({
            "nickname": r["nickname"],
            "total": total,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "wolf_win_rate": round(wolf_wins / wolf_total * 100, 1) if wolf_total else 0,
            "good_win_rate": round(good_wins / good_total * 100, 1) if good_total else 0,
        })
    return result


@router.get("/stats/by-model")
async def stats_by_model(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    repo = GameRepository(session)
    rows = await repo.get_stats_by_model()
    result = []
    for r in rows:
        total = r["total"] or 0
        wins = r["wins"] or 0
        wolf_total = r["wolf_total"] or 0
        wolf_wins = r["wolf_wins"] or 0
        good_total = r["good_total"] or 0
        good_wins = r["good_wins"] or 0
        result.append({
            "model_name": r["model_name"],
            "total": total,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "wolf_win_rate": round(wolf_wins / wolf_total * 100, 1) if wolf_total else 0,
            "good_win_rate": round(good_wins / good_total * 100, 1) if good_total else 0,
        })
    return result


@router.get("/stats/by-human")
async def stats_by_human(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    repo = GameRepository(session)
    rows = await repo.get_stats_by_human()
    result = []
    for r in rows:
        total = r["total"] or 0
        wins = r["wins"] or 0
        wolf_total = r["wolf_total"] or 0
        wolf_wins = r["wolf_wins"] or 0
        good_total = r["good_total"] or 0
        good_wins = r["good_wins"] or 0
        result.append({
            "nickname": r["nickname"],
            "total": total,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "wolf_win_rate": round(wolf_wins / wolf_total * 100, 1) if wolf_total else 0,
            "good_win_rate": round(good_wins / good_total * 100, 1) if good_total else 0,
        })
    return result
