import datetime as dt
from jose import jwt
from passlib.context import CryptContext
from fastapi import Request
from app.settings import settings

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(pw: str) -> str:
    return pwd_ctx.hash(pw)

def verify_password(pw: str, hashed: str) -> bool:
    return pwd_ctx.verify(pw, hashed)

def create_access_token(sub: str, minutes: int | None = None) -> str:
    expire_min = minutes or settings.ACCESS_TOKEN_EXPIRES_MIN
    now = dt.datetime.utcnow()
    payload = {"sub": sub, "iat": now, "exp": now + dt.timedelta(minutes=expire_min)}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALG)

def read_token_from_request(request: Request):
    token = request.cookies.get("access_token")
    if not token: return None
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALG])
    except Exception:
        return None
