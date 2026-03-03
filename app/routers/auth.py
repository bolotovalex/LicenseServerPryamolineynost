import datetime as dt
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import log_action
from app.config import security_config
from app.db import get_session
from app.models import AdminUser, Client, LoginAttempt, PasswordResetToken
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
    msg = request.query_params.get("msg", "")
    msg_type = request.query_params.get("msg_type", "success")
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": None,
        "flash_msg": msg,
        "flash_type": msg_type,
    })


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


# ── восстановление пароля ─────────────────────────────────────────────────────

@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_get(request: Request):
    return templates.TemplateResponse(
        "forgot_password.html", {"request": request, "info": None, "login": ""}
    )


@router.post("/forgot-password")
async def forgot_password_post(
    request: Request,
    login: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    ip = _get_ip(request)
    now = dt.datetime.utcnow()
    window_start = now - dt.timedelta(minutes=10)

    # Rate limit: 3 запроса с одного IP за 10 минут
    recent_count = (await db.execute(
        select(func.count()).where(
            LoginAttempt.ip_address == ip,
            LoginAttempt.login == "forgot-password",
            LoginAttempt.at >= window_start,
        )
    )).scalar() or 0

    if recent_count >= 3:
        return templates.TemplateResponse(
            "forgot_password.html",
            {"request": request,
             "info": "Если адрес зарегистрирован, письмо будет отправлено в течение минуты.",
             "login": login},
        )

    db.add(LoginAttempt(ip_address=ip, login="forgot-password", success=True))

    # ищем AdminUser по email, затем Client по login ИЛИ contact_email
    admin = (await db.execute(
        select(AdminUser).where(AdminUser.email == login)
    )).scalar_one_or_none()
    client = None
    if not admin:
        client = (await db.execute(
            select(Client).where(or_(Client.login == login, Client.contact_email == login))
        )).scalar_one_or_none()

    entity = admin or client
    if entity:
        entity_type = "admin" if admin else "org"
        email_to = admin.email if admin else client.contact_email

        if email_to:
            token = secrets.token_urlsafe(48)
            db.add(PasswordResetToken(
                entity_type=entity_type,
                entity_id=entity.id,
                token=token,
                expires_at=now + dt.timedelta(hours=1),
                ip_address=ip,
            ))
            await db.commit()

            reset_url = str(request.base_url).rstrip("/") + f"/reset-password?token={token}"
            from app.email import notify_password_reset
            notify_password_reset(email_to, reset_url)
        else:
            await db.commit()
    else:
        await db.commit()

    # одинаковый ответ — не раскрываем наличие аккаунта
    return templates.TemplateResponse(
        "forgot_password.html",
        {"request": request,
         "info": "Если адрес зарегистрирован, письмо будет отправлено в течение минуты.",
         "login": login},
    )


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_get(
    request: Request,
    token: str = "",
    db: AsyncSession = Depends(get_session),
):
    if not token:
        return RedirectResponse(
            url="/login?msg=Ссылка+недействительна+или+устарела&msg_type=error",
            status_code=303,
        )
    rec = await _get_valid_token(token, db)
    if not rec:
        return RedirectResponse(
            url="/login?msg=Ссылка+недействительна+или+устарела&msg_type=error",
            status_code=303,
        )
    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "valid_token": True, "errors": [], "token": token},
    )


@router.post("/reset-password")
async def reset_password_post(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    rec = await _get_valid_token(token, db)
    if not rec:
        return RedirectResponse(
            url="/login?msg=Ссылка+недействительна+или+устарела&msg_type=error",
            status_code=303,
        )

    if password != password2:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "valid_token": True,
             "errors": ["Пароли не совпадают"], "token": token},
            status_code=422,
        )

    errors = validate_password(password)
    if errors:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "valid_token": True, "errors": errors, "token": token},
            status_code=422,
        )

    # обновляем пароль нужной сущности
    if rec.entity_type == "admin":
        entity = (await db.execute(
            select(AdminUser).where(AdminUser.id == rec.entity_id)
        )).scalar_one_or_none()
    else:
        entity = (await db.execute(
            select(Client).where(Client.id == rec.entity_id)
        )).scalar_one_or_none()

    if entity:
        entity.password_hash = hash_password(password)
        entity.failed_attempts = 0
        entity.locked_until = None
        actor_login = entity.email if rec.entity_type == "admin" else (entity.login or entity.org_name)
    else:
        actor_login = None

    rec.used = True
    await db.flush()

    await log_action(
        db=db,
        actor_type=rec.entity_type,
        actor_id=rec.entity_id,
        actor_login=actor_login,
        action="password_reset",
        entity_type=rec.entity_type,
        entity_id=rec.entity_id,
        request=request,
    )
    await db.commit()

    return RedirectResponse(
        url="/login?msg=Пароль+успешно+изменён&msg_type=success",
        status_code=303,
    )


async def _get_valid_token(token: str, db: AsyncSession):
    """Возвращает PasswordResetToken если он действителен, иначе None."""
    now = dt.datetime.utcnow()
    rec = (await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token == token,
            PasswordResetToken.used == False,
            PasswordResetToken.expires_at > now,
        )
    )).scalar_one_or_none()
    return rec
