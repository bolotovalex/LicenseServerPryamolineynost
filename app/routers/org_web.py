"""
Личный кабинет организации (prefix=/org).
Защита: require_org — JWT cookie org_token.
Организация видит ТОЛЬКО свои данные (все запросы к БД фильтруются по client_id).
"""
import datetime as dt
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import log_action
from app.db import get_session
from app.models import Client, Feedback, License, LicenseAction
from app.password import validate_password
from app.security import get_current_org, hash_password, verify_password

router    = APIRouter()
templates = Jinja2Templates(directory="templates")


# ── helpers ───────────────────────────────────────────────────────────────────

def _flash(url: str, msg: str, msg_type: str = "success") -> RedirectResponse:
    sep    = "&" if "?" in url else "?"
    params = urlencode({"msg": msg, "msg_type": msg_type})
    return RedirectResponse(f"{url}{sep}{params}", status_code=303)


def _ctx(request: Request, org: Client, **extra) -> dict:
    return {
        "request": request,
        "org":     org,
        "now":     dt.datetime.utcnow(),
        **extra,
    }


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _require_org(request: Request, db: AsyncSession):
    """
    Проверяет авторизацию организации.
    Возвращает (org, None) при успехе, (None, RedirectResponse) иначе.
    """
    org = await get_current_org(request, db)
    if not org:
        return None, RedirectResponse("/login", status_code=303)
    return org, None


# ── dashboard ─────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_session)):
    org, redir = await _require_org(request, db)
    if redir:
        return redir

    licenses = (await db.execute(
        select(License)
        .where(License.client_id == org.id)
        .order_by(desc(License.issued_at))
    )).scalars().all()

    now = dt.datetime.utcnow()

    # Статистика
    total   = len(licenses)
    expired = sum(1 for l in licenses if l.expires_at and l.expires_at < now)
    active  = sum(
        1 for l in licenses
        if l.activated_at and not l.is_blocked
        and not (l.expires_at and l.expires_at < now)
    )

    ctx = _ctx(
        request, org,
        licenses=licenses,
        stats_total=total,
        stats_active=active,
        stats_expired=expired,
    )
    return templates.TemplateResponse("org/dashboard.html", ctx)


@router.post("/licenses/{license_id}/deactivate")
async def license_deactivate(
    request:    Request,
    license_id: int,
    db: AsyncSession = Depends(get_session),
):
    org, redir = await _require_org(request, db)
    if redir:
        return redir

    # Строго: только лицензия данной организации
    lic = (await db.execute(
        select(License).where(
            License.id == license_id,
            License.client_id == org.id,
        )
    )).scalar_one_or_none()

    if not lic:
        return _flash("/org/dashboard", "Лицензия не найдена", "error")
    if not lic.activated_at:
        return _flash("/org/dashboard", "Лицензия не активирована", "error")

    lic.activated_at       = None
    lic.device_id          = None
    lic.activation_payload = None
    lic.version            = (lic.version or 1) + 1

    db.add(LicenseAction(license_id=lic.id, action="deactivate"))
    await log_action(
        db=db,
        actor_type="org",
        action="deactivate",
        actor_id=org.id,
        actor_login=org.login,
        entity_type="license",
        entity_id=lic.id,
        details={"license_id": lic.id, "key_prefix": lic.key[:8] if lic.key else ""},
        success=True,
        request=request,
    )
    await db.commit()
    return _flash("/org/dashboard", "Лицензия деактивирована. Повторная активация доступна на любом устройстве.")


# ── профиль ───────────────────────────────────────────────────────────────────

@router.get("/profile", response_class=HTMLResponse)
async def profile_get(request: Request, db: AsyncSession = Depends(get_session)):
    org, redir = await _require_org(request, db)
    if redir:
        return redir
    return templates.TemplateResponse("org/profile.html", _ctx(request, org))


@router.post("/profile/change-password")
async def profile_change_password(
    request:          Request,
    current_password: str = Form(...),
    new_password:     str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    org, redir = await _require_org(request, db)
    if redir:
        return redir

    errors = []

    if not org.password_hash or not verify_password(current_password, org.password_hash):
        errors.append("Неверный текущий пароль")
    if new_password != confirm_password:
        errors.append("Новые пароли не совпадают")
    elif pw_errs := validate_password(new_password):
        errors.extend(pw_errs)

    if errors:
        return _flash("/org/profile", "; ".join(errors), "error")

    org.password_hash = hash_password(new_password)
    await log_action(
        db=db,
        actor_type="org",
        action="change_password",
        actor_id=org.id,
        actor_login=org.login,
        success=True,
        request=request,
    )
    await db.commit()
    return _flash("/org/profile", "Пароль успешно изменён")


# ── обратная связь ────────────────────────────────────────────────────────────

@router.get("/feedback", response_class=HTMLResponse)
async def feedback_list(request: Request, db: AsyncSession = Depends(get_session)):
    org, redir = await _require_org(request, db)
    if redir:
        return redir

    items = (await db.execute(
        select(Feedback)
        .where(Feedback.entity_type == "org", Feedback.entity_id == org.id)
        .order_by(desc(Feedback.created_at))
    )).scalars().all()

    ctx = _ctx(request, org, items=items)
    return templates.TemplateResponse("org/feedback_list.html", ctx)


@router.post("/feedback/new")
async def feedback_new(
    request: Request,
    subject: str = Form(...),
    message: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    org, redir = await _require_org(request, db)
    if redir:
        return redir

    if not subject.strip() or not message.strip():
        return _flash("/org/feedback", "Тема и текст обращения обязательны", "error")

    db.add(Feedback(
        entity_type="org",
        entity_id=org.id,
        org_name=org.org_name,
        contact_email=org.contact_email,
        subject=subject.strip(),
        message=message.strip(),
        ip_address=_get_ip(request),
        user_agent=request.headers.get("User-Agent"),
    ))
    await db.commit()
    return _flash("/org/feedback", "Обращение отправлено. Мы ответим в ближайшее время.")
