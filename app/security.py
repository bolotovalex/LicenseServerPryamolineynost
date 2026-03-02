import datetime as dt
from jose import jwt
from passlib.context import CryptContext
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import app_config

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(pw: str) -> str:
    return pwd_ctx.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    return pwd_ctx.verify(pw, hashed)


def _make_token(payload: dict) -> str:
    return jwt.encode(payload, app_config.secret_key, algorithm=app_config.jwt_algorithm)


def _read_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, app_config.secret_key, algorithms=[app_config.jwt_algorithm])
    except Exception:
        return None


# ── AdminUser (owner) ─────────────────────────────────────────────────────────

def create_owner_token(user) -> str:
    now = dt.datetime.utcnow()
    exp = now + dt.timedelta(minutes=app_config.token_expires_minutes)
    return _make_token({"sub": user.email, "role": "owner", "iat": now, "exp": exp})


async def get_current_owner(request: Request, db: AsyncSession):
    token = request.cookies.get("owner_token")
    if not token:
        return None
    payload = _read_token(token)
    if not payload or payload.get("role") != "owner":
        return None
    from app.models import AdminUser
    user = (await db.execute(
        select(AdminUser).where(AdminUser.email == payload["sub"])
    )).scalar_one_or_none()
    return user if (user and user.is_active) else None


async def require_owner(request: Request, db: AsyncSession):
    user = await get_current_owner(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


# ── Client (org) ──────────────────────────────────────────────────────────────

def create_org_token(client) -> str:
    now = dt.datetime.utcnow()
    exp = now + dt.timedelta(minutes=app_config.token_expires_minutes)
    return _make_token({"sub": str(client.id), "role": "org", "iat": now, "exp": exp})


async def get_current_org(request: Request, db: AsyncSession):
    token = request.cookies.get("org_token")
    if not token:
        return None
    payload = _read_token(token)
    if not payload or payload.get("role") != "org":
        return None
    from app.models import Client
    client = (await db.execute(
        select(Client).where(Client.id == int(payload["sub"]))
    )).scalar_one_or_none()
    return client if (client and client.is_active) else None


async def require_org(request: Request, db: AsyncSession):
    client = await get_current_org(request, db)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return client


# ── Legacy (backward compat) ──────────────────────────────────────────────────

def create_access_token(sub: str, minutes: int | None = None) -> str:
    from app.settings import settings
    expire_min = minutes or settings.ACCESS_TOKEN_EXPIRES_MIN
    now = dt.datetime.utcnow()
    payload = {"sub": sub, "iat": now, "exp": now + dt.timedelta(minutes=expire_min)}
    return _make_token(payload)


def read_token_from_request(request: Request) -> dict | None:
    token = request.cookies.get("access_token")
    if not token:
        return None
    return _read_token(token)
