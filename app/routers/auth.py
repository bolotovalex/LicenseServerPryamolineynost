import datetime as dt

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import security_config
from app.db import get_session
from app.models import AdminUser, Client, LoginAttempt
from app.password import validate_password
from app.security import (
    create_org_token, create_owner_token,
    get_current_org, get_current_owner,
    hash_password, verify_password,
)

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── root ──────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def root(request: Request, db: AsyncSession = Depends(get_session)):
    if await get_current_owner(request, db):
        return RedirectResponse(url="/owner/dashboard", status_code=302)
    if await get_current_org(request, db):
        return RedirectResponse(url="/org/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


# ── login / logout ────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login_post(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    ip = _get_ip(request)
    now = dt.datetime.utcnow()
    window_start = now - dt.timedelta(minutes=security_config.attempt_window_minutes)

    # 1. IP rate limit
    failed_from_ip = (await db.execute(
        select(func.count()).where(
            LoginAttempt.ip_address == ip,
            LoginAttempt.success == False,
            LoginAttempt.at >= window_start,
        )
    )).scalar() or 0

    if failed_from_ip >= security_config.max_attempts:
        return templates.TemplateResponse(
            "login.html",
            {"request": request,
             "error": f"Слишком много попыток. Попробуйте через {security_config.attempt_window_minutes} минут"},
            status_code=429,
        )

    # 2. Найти сущность: сначала AdminUser по email, затем Client по login
    admin = (await db.execute(
        select(AdminUser).where(AdminUser.email == login)
    )).scalar_one_or_none()

    client = None
    if not admin:
        client = (await db.execute(
            select(Client).where(Client.login == login)
        )).scalar_one_or_none()

    entity = admin or client
    is_valid = bool(
        entity and entity.password_hash and verify_password(password, entity.password_hash)
    )

    # 3. Неверные данные
    if not is_valid:
        db.add(LoginAttempt(ip_address=ip, login=login, success=False))
        if entity:
            entity.failed_attempts = (entity.failed_attempts or 0) + 1
            if entity.failed_attempts >= security_config.max_attempts:
                entity.locked_until = now + dt.timedelta(minutes=security_config.lockout_minutes)
        await db.commit()
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный логин или пароль"},
            status_code=401,
        )

    # 4. Проверка блокировки аккаунта
    if entity.locked_until and entity.locked_until > now:
        locked_str = entity.locked_until.strftime("%H:%M")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": f"Аккаунт заблокирован до {locked_str}"},
            status_code=403,
        )

    # 5. Проверка is_active
    if not entity.is_active:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Аккаунт деактивирован"},
            status_code=403,
        )

    # 6. Успешный вход
    db.add(LoginAttempt(ip_address=ip, login=login, success=True))
    entity.failed_attempts = 0
    entity.last_login_at = now
    await db.commit()

    if admin:
        resp = RedirectResponse(url="/owner/dashboard", status_code=303)
        resp.set_cookie("owner_token", create_owner_token(admin), httponly=True, samesite="lax")
    else:
        resp = RedirectResponse(url="/org/dashboard", status_code=303)
        resp.set_cookie("org_token", create_org_token(client), httponly=True, samesite="lax")

    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("owner_token")
    resp.delete_cookie("org_token")
    return resp


# ── setup (первый администратор) ──────────────────────────────────────────────

@router.get("/setup", response_class=HTMLResponse)
async def setup_get(request: Request, db: AsyncSession = Depends(get_session)):
    if (await db.execute(select(AdminUser))).scalars().first():
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("setup.html", {"request": request, "errors": [], "email": ""})


@router.post("/setup")
async def setup_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    if (await db.execute(select(AdminUser))).scalars().first():
        return RedirectResponse(url="/login", status_code=302)

    errors = validate_password(password)
    if errors:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "errors": errors, "email": email},
            status_code=422,
        )

    db.add(AdminUser(email=email, password_hash=hash_password(password), role="superadmin"))
    await db.commit()
    return RedirectResponse(url="/login", status_code=303)
