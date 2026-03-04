"""
Личный кабинет организации (prefix=/org).
Защита: require_org — JWT cookie org_token.
Организация видит ТОЛЬКО свои данные (все запросы к БД фильтруются по client_id).
"""
import datetime as dt
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import log_action
from app.db import get_session
from sqlalchemy.orm import selectinload

from app.models import Client, Feedback, FeedbackMessage, License, LicenseAction, LicenseKey
from app.password import validate_password
from app.security import get_current_org, hash_password, verify_password
from app.utils import generate_license_key

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


@router.post("/licenses/generate")
async def org_license_generate(
    request:     Request,
    description: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    org, redir = await _require_org(request, db)
    if redir:
        return redir

    total = (await db.execute(
        select(func.count(License.id)).where(License.client_id == org.id)
    )).scalar_one()

    if total >= org.max_keys:
        return _flash("/org/dashboard", "Квота исчерпана — обратитесь к администратору", "error")

    exp = None
    if org.key_ttl_days:
        exp = dt.datetime.utcnow() + dt.timedelta(days=org.key_ttl_days)

    key = generate_license_key()
    lic = License(
        client_id=org.id,
        version=1,
        key=key,
        expires_at=exp,
        description=description.strip() or "автоматическая генерация",
    )
    db.add(lic)
    await db.flush()
    db.add(LicenseKey(license_id=lic.id, key=key, is_active=True))
    db.add(LicenseAction(license_id=lic.id, action="issue"))
    await log_action(
        db=db,
        actor_type="org",
        action="issue",
        actor_id=org.id,
        actor_login=org.login,
        entity_type="license",
        entity_id=lic.id,
        success=True,
        request=request,
    )
    await db.commit()
    return _flash("/org/dashboard", "Лицензия выпущена")


@router.post("/licenses/{license_id}/edit")
async def org_license_edit(
    request:     Request,
    license_id:  int,
    description: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    org, redir = await _require_org(request, db)
    if redir:
        return redir

    lic = (await db.execute(
        select(License).where(
            License.id == license_id,
            License.client_id == org.id,
        )
    )).scalar_one_or_none()

    if not lic:
        return _flash("/org/dashboard", "Лицензия не найдена", "error")

    if description.strip():
        lic.description = description.strip()
        await db.commit()
        return _flash("/org/dashboard", "Описание обновлено")
    return _flash("/org/dashboard", "Описание не изменено", "warn")


@router.post("/licenses/{license_id}/reset")
async def org_license_reset(
    request:    Request,
    license_id: int,
    db: AsyncSession = Depends(get_session),
):
    org, redir = await _require_org(request, db)
    if redir:
        return redir

    lic = (await db.execute(
        select(License).where(
            License.id == license_id,
            License.client_id == org.id,
        )
    )).scalar_one_or_none()

    if not lic:
        return _flash("/org/dashboard", "Лицензия не найдена", "error")

    now = dt.datetime.now(dt.UTC)
    st  = lic.computed_status(now)
    if st not in ("released", "not_activated"):
        return _flash("/org/dashboard", "Сброс доступен только для деактивированных и неактивированных лицензий", "error")

    # Деактивировать текущий активный ключ в истории
    active_key = (await db.execute(
        select(LicenseKey).where(
            LicenseKey.license_id == lic.id,
            LicenseKey.is_active == True,
        )
    )).scalar_one_or_none()
    if active_key:
        active_key.is_active = False
        active_key.deactivated_at = now
        active_key.reason = "reset by org"

    new_key = generate_license_key()
    db.add(LicenseKey(license_id=lic.id, key=new_key, is_active=True))
    lic.key               = new_key
    lic.status            = "not_activated"
    lic.device_id         = None
    lic.device_name       = None
    lic.device_comment    = None
    lic.activation_payload = None
    lic.activated_at      = None
    lic.version           = (lic.version or 0) + 1

    db.add(LicenseAction(
        license_id=lic.id,
        action="reset",
        actor=org.login,
        reason="reset by org",
    ))
    await log_action(
        db=db,
        actor_type="org",
        action="license_reset",
        actor_id=org.id,
        actor_login=org.login,
        entity_type="license",
        entity_id=lic.id,
        success=True,
        request=request,
    )
    await db.commit()
    return _flash("/org/dashboard", "Ключ сброшен — скопируйте новый ключ для активации")


@router.get("/licenses/{license_id}/history")
async def org_license_history(
    request:    Request,
    license_id: int,
    db: AsyncSession = Depends(get_session),
):
    org, redir = await _require_org(request, db)
    if redir:
        raise HTTPException(401)

    lic = (await db.execute(
        select(License)
        .where(License.id == license_id, License.client_id == org.id)
        .options(selectinload(License.actions))
    )).scalar_one_or_none()

    if not lic:
        raise HTTPException(404)

    actions = sorted(lic.actions, key=lambda a: a.at, reverse=True)
    return JSONResponse([
        {
            "action": a.action,
            "at":     a.at.isoformat()[:16],
            "actor":  a.actor  or "—",
            "reason": a.reason or "",
        }
        for a in actions[:30]
    ])


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
        .options(selectinload(Feedback.messages))
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


@router.get("/feedback/{feedback_id}", response_class=HTMLResponse)
async def feedback_detail(
    request:     Request,
    feedback_id: int,
    db: AsyncSession = Depends(get_session),
):
    org, redir = await _require_org(request, db)
    if redir:
        return redir

    fb = (await db.execute(
        select(Feedback)
        .options(selectinload(Feedback.messages))
        .where(
            Feedback.id == feedback_id,
            Feedback.entity_type == "org",
            Feedback.entity_id == org.id,
        )
    )).scalar_one_or_none()

    if not fb:
        return _flash("/org/feedback", "Обращение не найдено", "error")

    ctx = _ctx(request, org, fb=fb)
    return templates.TemplateResponse("org/feedback_detail.html", ctx)


@router.post("/feedback/{feedback_id}/reply")
async def feedback_reply(
    request:     Request,
    feedback_id: int,
    message:     str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    org, redir = await _require_org(request, db)
    if redir:
        return redir

    fb = (await db.execute(
        select(Feedback).where(
            Feedback.id == feedback_id,
            Feedback.entity_type == "org",
            Feedback.entity_id == org.id,
        )
    )).scalar_one_or_none()

    if not fb:
        return _flash("/org/feedback", "Обращение не найдено", "error")

    if not message.strip():
        return _flash(f"/org/feedback/{feedback_id}", "Текст ответа не может быть пустым", "error")

    db.add(FeedbackMessage(
        feedback_id=fb.id,
        sender_type="org",
        sender_id=org.id,
        sender_name=org.org_name,
        message=message.strip(),
    ))
    # Org replied → статус "read" (ждёт нового ответа admin)
    if fb.status == "answered":
        fb.status = "read"
    await db.commit()

    from app.config import smtp_config
    from app.email import notify_feedback_reply_to_admin
    if smtp_config.from_addr:
        notify_feedback_reply_to_admin(
            to=smtp_config.from_addr,
            org_name=org.org_name,
            subject=fb.subject,
            reply_text=message.strip(),
            admin_url=f"/owner/feedback/{feedback_id}",
        )

    return _flash(f"/org/feedback/{feedback_id}", "Ответ отправлен")
