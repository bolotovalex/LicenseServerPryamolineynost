"""
Роутер владельца/администратора (prefix=/owner).
Защита: require_owner (JWT cookie owner_token).
Superadmin-only разделы: /admins, /logs.
"""
import csv
import datetime as dt
import io
import re
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from PIL import Image
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit import log_action
from app.db import get_session
from app.models import AdminUser, AuditLog, Client, Feedback, FeedbackMessage, License, LicenseAction, LicenseKey
from app.password import generate_password, validate_password
from app.security import hash_password, require_owner, verify_password
from app.utils import generate_license_key, make_qr_png

BACKUP_DIR = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)
_SAFE_NAME = re.compile(r'^[\w\-. ]+\.json$')

LOGO_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
LOGO_MAX_BYTES   = 2 * 1024 * 1024
LOGO_MAX_DIM     = 512

router    = APIRouter()
templates = Jinja2Templates(directory="templates")


# ── helpers ───────────────────────────────────────────────────────────────────

def _flash(url: str, msg: str, msg_type: str = "success") -> RedirectResponse:
    sep    = "&" if "?" in url else "?"
    params = urlencode({"msg": msg, "msg_type": msg_type})
    return RedirectResponse(f"{url}{sep}{params}", status_code=303)


async def _feedback_count(db: AsyncSession) -> int:
    return (await db.execute(
        select(func.count(Feedback.id)).where(Feedback.status == "new")
    )).scalar_one()


async def _ctx(request: Request, owner, db: AsyncSession, **extra) -> dict:
    """Базовый контекст для всех шаблонов owner/."""
    return {
        "request":        request,
        "admin":          owner,
        "feedback_count": await _feedback_count(db),
        "now":            dt.datetime.utcnow(),
        **extra,
    }


def _superadmin_check(owner) -> RedirectResponse | None:
    """Возвращает редирект с ошибкой если пользователь не superadmin."""
    if owner.role != "superadmin":
        return _flash("/owner/dashboard", "Доступ запрещён — требуется роль superadmin", "error")
    return None


# ── dashboard ─────────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)

    rows = (await db.execute(
        select(
            Client.id,
            Client.org_name,
            Client.contact_email,
            Client.max_keys,
            Client.is_active,
            func.count(License.id).label("keys_count"),
        )
        .outerjoin(License, (License.client_id == Client.id) & (License.deleted_at == None) & (License.status == "activated"))
        .where(Client.deleted_at == None)
        .group_by(Client.id)
        .order_by(Client.id)
    )).all()

    ctx = await _ctx(request, owner, db, rows=rows)
    return templates.TemplateResponse("owner/dashboard.html", ctx)


# ── clients ───────────────────────────────────────────────────────────────────

@router.get("/clients", response_class=HTMLResponse)
async def clients_list(
    request:      Request,
    db:           AsyncSession = Depends(get_session),
    show_deleted: bool = False,
):
    owner = await require_owner(request, db)
    q = select(Client).options(selectinload(Client.licenses)).order_by(Client.id)
    if show_deleted:
        q = q.where(Client.deleted_at.isnot(None))
    else:
        q = q.where(Client.deleted_at.is_(None))
    clients = (await db.execute(q)).scalars().all()
    ctx = await _ctx(request, owner, db, clients=clients, show_deleted=show_deleted)
    return templates.TemplateResponse("owner/client_list.html", ctx)


@router.post("/clients/new")
async def clients_new(
    request: Request,
    org_name:      str = Form(...),
    login:         str = Form(...),
    password:      str = Form(""),
    contact_email: str = Form(""),
    notes:         str = Form(""),
    max_keys:      int = Form(5),
    key_ttl_days:  str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)

    plain  = password or generate_password()
    errors = validate_password(plain)
    if errors:
        return _flash("/owner/clients", "; ".join(errors), "error")

    existing = (await db.execute(
        select(Client).where(Client.login == login)
    )).scalar_one_or_none()
    if existing:
        return _flash("/owner/clients", f"Логин «{login}» уже занят", "error")

    ttl    = int(key_ttl_days) if key_ttl_days else None
    client = Client(
        org_name=org_name,
        login=login,
        password_hash=hash_password(plain),
        contact_email=contact_email or None,
        notes=notes or None,
        max_keys=max_keys,
        key_ttl_days=ttl,
        created_by=owner.id,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)

    if client.contact_email:
        from app.email import notify_org_created
        notify_org_created(client, plain)

    if not password:
        return _flash(f"/owner/clients/{client.id}", f"Клиент создан. Пароль для входа: {plain}", "success")
    return RedirectResponse(url=f"/owner/clients/{client.id}", status_code=303)


@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(request: Request, client_id: int, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)

    client = (await db.execute(
        select(Client).where(Client.id == client_id)
    )).scalar_one_or_none()
    if not client:
        raise HTTPException(404)

    licenses = (await db.execute(
        select(License)
        .where(License.client_id == client_id, License.deleted_at.is_(None))
        .options(selectinload(License.keys))
        .order_by(desc(License.issued_at))
    )).scalars().all()

    deleted_licenses = (await db.execute(
        select(License)
        .where(License.client_id == client_id, License.deleted_at.isnot(None))
        .order_by(desc(License.deleted_at))
    )).scalars().all()

    # История ключей для модалки
    keys_payloads = {
        lic.id: [
            {
                "key":            k.key,
                "active":         bool(k.is_active),
                "issued_at":      k.issued_at.isoformat() if k.issued_at else None,
                "deactivated_at": k.deactivated_at.isoformat() if k.deactivated_at else None,
                "reason":         k.reason,
            }
            for k in lic.keys
        ]
        for lic in licenses
    }

    # Лимиты генерации
    total_keys  = len(licenses)
    available   = max(0, client.max_keys - total_keys)
    max_allowed = min(50, available)

    # Блок 4: журнал (LicenseAction + AuditLog)
    license_ids = [l.id for l in licenses]
    log_entries: list[dict] = []
    lic_key: dict = {}
    actions_payloads: dict = {}

    if license_ids:
        actions = (await db.execute(
            select(LicenseAction)
            .where(LicenseAction.license_id.in_(license_ids))
            .order_by(desc(LicenseAction.at))
            .limit(200)
        )).scalars().all()

        lic_desc = {l.id: l.description for l in licenses}
        lic_key  = {l.id: l.key          for l in licenses}

        # История действий для модалки (группируем по license_id)
        for a in actions:
            lid = a.license_id
            if lid not in actions_payloads:
                actions_payloads[lid] = []
            if len(actions_payloads[lid]) < 20:
                actions_payloads[lid].append({
                    "action": a.action,
                    "at":     a.at.strftime('%Y-%m-%d %H:%M'),
                    "reason": a.reason or "",
                    "actor":  a.actor or "",
                })

        for a in actions:
            log_entries.append({
                "at":         a.at,
                "action":     a.action,
                "license_id": a.license_id,
                "desc":       lic_desc.get(a.license_id, ""),
                "reason":     a.reason,
                "actor":      None,
                "ip":         None,
                "success":    True,
                "source":     "action",
            })

        audit_rows = (await db.execute(
            select(AuditLog)
            .where(AuditLog.entity_type == "license", AuditLog.entity_id.in_(license_ids))
            .order_by(desc(AuditLog.at))
            .limit(50)
        )).scalars().all()

        for a in audit_rows:
            log_entries.append({
                "at":         a.at,
                "action":     a.action,
                "license_id": a.entity_id,
                "desc":       a.details or "",
                "reason":     None,
                "actor":      a.actor_login or a.actor_type,
                "ip":         a.ip_address,
                "success":    a.success,
                "source":     "audit",
            })

    log_entries.sort(key=lambda x: x["at"], reverse=True)
    log_entries = log_entries[:50]

    # Создатель клиента (если есть)
    creator = None
    if client.created_by:
        creator = (await db.execute(
            select(AdminUser).where(AdminUser.id == client.created_by)
        )).scalar_one_or_none()

    # Дата по умолчанию для срока (today + key_ttl_days)
    default_expires = ""
    if client.key_ttl_days:
        default_expires = (dt.date.today() + dt.timedelta(days=client.key_ttl_days)).isoformat()

    ctx = await _ctx(
        request, owner, db,
        client=client,
        licenses=licenses,
        keys_payloads=keys_payloads,
        actions_payloads=actions_payloads,
        total_keys=total_keys,
        available=available,
        max_allowed=max_allowed,
        deleted_licenses=deleted_licenses,
        log_entries=log_entries,
        creator=creator,
        default_expires=default_expires,
        lic_key=lic_key,
    )
    return templates.TemplateResponse("owner/client_detail.html", ctx)


@router.post("/clients/{client_id}/update-info")
async def client_update_info(
    request: Request,
    client_id:     int,
    org_name:      str = Form(...),
    notes:         str = Form(""),
    contact_email: str = Form(""),
    max_keys:      int = Form(5),
    key_ttl_days:  str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    client = (await db.execute(
        select(Client).where(Client.id == client_id)
    )).scalar_one_or_none()
    if not client:
        raise HTTPException(404)

    # Считаем активные (не удалённые) лицензии до сохранения
    total_active = (await db.execute(
        select(func.count(License.id)).where(
            License.client_id == client_id,
            License.deleted_at.is_(None),
        )
    )).scalar_one()

    client.org_name      = org_name
    client.notes         = notes or None
    client.contact_email = contact_email or None
    client.max_keys      = max_keys
    client.key_ttl_days  = int(key_ttl_days) if key_ttl_days else None
    await db.commit()

    if total_active > max_keys:
        over = total_active - max_keys
        return _flash(
            f"/owner/clients/{client_id}",
            f"Квота уменьшена до {max_keys}. Выпущено {total_active} лицензий — "
            f"{over} шт. превышают квоту. Заблокируйте лишние через меню ⋮",
            "warn",
        )
    return _flash(f"/owner/clients/{client_id}", "Информация обновлена")


@router.post("/clients/{client_id}/reset-password")
async def client_reset_password(
    request: Request,
    client_id: int,
    new_password: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    client = (await db.execute(
        select(Client).where(Client.id == client_id)
    )).scalar_one_or_none()
    if not client:
        raise HTTPException(404)

    plain = new_password.strip() or generate_password()
    client.password_hash = hash_password(plain)
    await db.commit()

    if client.contact_email:
        from app.email import send_email
        import asyncio
        asyncio.create_task(send_email(
            client.contact_email,
            "Обновлён пароль доступа",
            f"<p>Новый пароль для входа в личный кабинет: <b>{plain}</b></p>",
        ))

    return _flash(f"/owner/clients/{client_id}", "Пароль клиента успешно изменён")


@router.post("/clients/{client_id}/toggle-active")
async def client_toggle_active(
    request: Request,
    client_id: int,
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    client = (await db.execute(
        select(Client).where(Client.id == client_id)
    )).scalar_one_or_none()
    if not client:
        raise HTTPException(404)
    client.is_active = not client.is_active
    await db.commit()
    status = "активирован" if client.is_active else "отключён"
    return _flash(f"/owner/clients/{client_id}", f"Клиент {status}")


# оставляем обратную совместимость
@router.post("/clients/{client_id}/deactivate")
async def client_deactivate(request: Request, client_id: int, db: AsyncSession = Depends(get_session)):
    await require_owner(request, db)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)
    client.is_active = False
    await db.commit()
    return RedirectResponse(url=f"/owner/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/activate")
async def client_activate(request: Request, client_id: int, db: AsyncSession = Depends(get_session)):
    await require_owner(request, db)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)
    client.is_active = True
    await db.commit()
    return RedirectResponse(url=f"/owner/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/delete")
async def client_delete(request: Request, client_id: int, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client or client.deleted_at is not None:
        raise HTTPException(404)

    client.deleted_at = dt.datetime.utcnow()
    await log_action(
        db=db,
        actor_type="admin",
        action="delete_client",
        actor_id=owner.id,
        actor_login=owner.email,
        entity_type="client",
        entity_id=client.id,
        success=True,
        request=request,
    )
    await db.commit()
    return _flash("/owner/clients", f"Клиент «{client.org_name}» удалён. Можно восстановить из архива.")


@router.post("/clients/{client_id}/restore")
async def client_restore(request: Request, client_id: int, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client or client.deleted_at is None:
        raise HTTPException(404)

    client.deleted_at = None
    await log_action(
        db=db,
        actor_type="admin",
        action="restore_client",
        actor_id=owner.id,
        actor_login=owner.email,
        entity_type="client",
        entity_id=client.id,
        success=True,
        request=request,
    )
    await db.commit()
    return _flash(f"/owner/clients/{client.id}", f"Организация «{client.org_name}» восстановлена")


@router.post("/clients/{client_id}/generate")
async def client_generate_keys(
    request: Request,
    client_id:   int,
    count:       int = Form(1),
    description: str = Form(""),
    expires_at:  str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)

    total     = (await db.execute(select(func.count(License.id)).where(
        License.client_id == client_id, License.deleted_at.is_(None)
    ))).scalar_one()
    available = max(0, client.max_keys - total)

    if count < 1 or count > 50:
        return _flash(f"/owner/clients/{client_id}", "Количество должно быть от 1 до 50", "error")
    if count > available:
        return _flash(f"/owner/clients/{client_id}", f"Квота исчерпана (доступно {available} из {client.max_keys})", "error")

    exp   = dt.datetime.fromisoformat(expires_at) if expires_at else None
    desc  = description.strip() or "автоматическая генерация"
    new_lics  = []

    for _ in range(count):
        key = generate_license_key()
        lic = License(client_id=client_id, version=1, key=key, expires_at=exp, description=desc)
        db.add(lic)
        await db.flush()
        db.add(LicenseKey(license_id=lic.id, key=key, is_active=True))
        db.add(LicenseAction(license_id=lic.id, action="issue"))
        new_lics.append(lic)

    await db.commit()

    if client.contact_email:
        from app.email import notify_key_issued
        for nl in new_lics:
            notify_key_issued(client, nl)

    word = "лицензия" if count == 1 else ("лицензии" if count < 5 else "лицензий")
    return _flash(f"/owner/clients/{client_id}", f"Выпущено {count} {word}")


# ── логотип ───────────────────────────────────────────────────────────────────

@router.get("/clients/{client_id}/logo")
async def client_logo(client_id: int, db: AsyncSession = Depends(get_session)):
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client or not client.logo_data:
        raise HTTPException(404)
    return Response(content=client.logo_data, media_type=client.logo_mime or "image/png")


@router.post("/clients/{client_id}/upload-logo")
async def client_upload_logo(
    request: Request,
    client_id: int,
    logo: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)

    ext = Path(logo.filename).suffix.lower() if logo.filename else ""
    if ext not in LOGO_ALLOWED_EXT:
        raise HTTPException(400, "Недопустимый формат. Разрешены: jpg, png, gif, webp")

    data = await logo.read()
    if len(data) > LOGO_MAX_BYTES:
        raise HTTPException(400, "Файл слишком большой (максимум 2 МБ)")

    img      = Image.open(io.BytesIO(data))
    has_alpha = img.mode in ("RGBA", "P", "LA")
    img      = img.convert("RGBA" if has_alpha else "RGB")
    if img.width > LOGO_MAX_DIM or img.height > LOGO_MAX_DIM:
        img.thumbnail((LOGO_MAX_DIM, LOGO_MAX_DIM), Image.LANCZOS)

    buf = io.BytesIO()
    if has_alpha:
        img.save(buf, format="PNG", optimize=True)
        mime = "image/png"
    else:
        img.save(buf, format="JPEG", quality=85, optimize=True)
        mime = "image/jpeg"

    client.logo_data = buf.getvalue()
    client.logo_mime = mime
    await db.commit()
    return _flash(f"/owner/clients/{client_id}", "Логотип обновлён")


@router.post("/clients/{client_id}/delete-logo")
async def client_delete_logo(
    request: Request,
    client_id: int,
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)
    client.logo_data = None
    client.logo_mime = None
    await db.commit()
    return RedirectResponse(url=f"/owner/clients/{client_id}", status_code=303)


# ── лицензии ──────────────────────────────────────────────────────────────────

@router.post("/licenses/issue")
async def license_issue(
    request: Request,
    client_id:   int = Form(...),
    description: str = Form(...),
    expires_at:  str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    exp = dt.datetime.fromisoformat(expires_at) if expires_at else None
    key = generate_license_key()
    lic = License(client_id=client_id, version=1, key=key, expires_at=exp, description=description)
    db.add(lic)
    await db.flush()
    db.add(LicenseKey(license_id=lic.id, key=key, is_active=True))
    db.add(LicenseAction(license_id=lic.id, action="issue"))
    await db.commit()
    return _flash(f"/owner/clients/{client_id}", "Лицензия выпущена")


@router.post("/licenses/{license_id}/reset")
async def license_reset(
    request: Request,
    license_id: int,
    reason:     str = Form(...),
    expires_at: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404)

    active_key = (await db.execute(
        select(LicenseKey).where(LicenseKey.license_id == lic.id, LicenseKey.is_active == True)
    )).scalars().first()
    if active_key:
        active_key.is_active      = False
        active_key.deactivated_at = dt.datetime.utcnow()
        active_key.reason         = reason

    new_key               = generate_license_key()
    lic.version           = (lic.version or 1) + 1
    lic.key               = new_key
    lic.status            = "not_activated"
    lic.activated_at      = None
    lic.device_id         = None
    lic.device_name       = None
    lic.device_comment    = None
    lic.activation_payload = None
    if expires_at:
        lic.expires_at = dt.datetime.fromisoformat(expires_at)

    db.add(LicenseKey(license_id=lic.id, key=new_key, is_active=True))
    db.add(LicenseAction(license_id=lic.id, action="reset", reason=reason))
    await log_action(
        db=db,
        actor_type="admin",
        action="license_reset",
        actor_id=owner.id,
        actor_login=owner.email,
        entity_type="license",
        entity_id=lic.id,
        success=True,
        request=request,
    )
    await db.commit()

    client = await db.get(Client, lic.client_id)
    if client and client.contact_email:
        from app.email import notify_key_reset
        notify_key_reset(client, lic, reason)

    return _flash(f"/owner/clients/{lic.client_id}", "Лицензия сброшена")


@router.post("/licenses/{license_id}/block")
async def license_block(
    request: Request,
    license_id: int,
    reason:     str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404)
    lic.is_blocked   = True
    lic.block_reason = reason
    db.add(LicenseAction(license_id=lic.id, action="block", reason=reason))
    await db.commit()

    client = await db.get(Client, lic.client_id)
    if client and client.contact_email:
        from app.email import notify_key_blocked
        notify_key_blocked(client, lic, reason)

    return _flash(f"/owner/clients/{lic.client_id}", "Лицензия заблокирована", "warn")


@router.post("/licenses/{license_id}/unblock")
async def license_unblock(
    request: Request,
    license_id: int,
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404)
    lic.is_blocked   = False
    lic.block_reason = None
    db.add(LicenseAction(license_id=lic.id, action="unblock"))
    await db.commit()
    return _flash(f"/owner/clients/{lic.client_id}", "Лицензия разблокирована")


@router.get("/licenses/{license_id}/qrcode")
async def license_qr(request: Request, license_id: int, db: AsyncSession = Depends(get_session)):
    await require_owner(request, db)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404)
    return Response(content=make_qr_png(lic.key), media_type="image/png")


@router.post("/licenses/{license_id}/delete")
async def license_soft_delete(
    request: Request,
    license_id: int,
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic or lic.deleted_at is not None:
        raise HTTPException(404)
    lic.deleted_at = dt.datetime.utcnow()
    # Снимаем активацию при удалении
    if lic.status == "activated":
        lic.status = "released"
        lic.device_id = None
        lic.device_name = None
    db.add(LicenseAction(license_id=lic.id, action="delete", actor=owner.email))
    await db.commit()
    return _flash(f"/owner/clients/{lic.client_id}", "Лицензия удалена")


# ── администраторы (только superadmin) ───────────────────────────────────────

@router.get("/admins", response_class=HTMLResponse)
async def admins_list(request: Request, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)
    if redir := _superadmin_check(owner):
        return redir
    admins = (await db.execute(select(AdminUser).order_by(AdminUser.id))).scalars().all()
    ctx    = await _ctx(request, owner, db, admins=admins)
    return templates.TemplateResponse("owner/admin_list.html", ctx)


@router.get("/admins/new", response_class=HTMLResponse)
async def admins_new_get(request: Request, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)
    if redir := _superadmin_check(owner):
        return redir
    ctx = await _ctx(request, owner, db)
    return templates.TemplateResponse("owner/admin_form.html", ctx)


@router.post("/admins/new")
async def admins_new_post(
    request: Request,
    email:    str = Form(...),
    password: str = Form(""),
    role:     str = Form("admin"),
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)
    if redir := _superadmin_check(owner):
        return redir

    plain  = password or generate_password()
    errors = validate_password(plain)
    if errors:
        return _flash("/owner/admins/new", "; ".join(errors), "error")

    existing = (await db.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none()
    if existing:
        return _flash("/owner/admins/new", f"Email «{email}» уже занят", "error")

    if role not in ("admin", "superadmin"):
        role = "admin"

    user = AdminUser(
        email=email,
        password_hash=hash_password(plain),
        role=role,
        created_by=owner.id,
    )
    db.add(user)
    await db.commit()

    if not password:
        return _flash("/owner/admins", f"Администратор создан. Пароль: {plain}")
    return _flash("/owner/admins", "Администратор создан")


@router.post("/admins/{admin_id}/reset-password")
async def admin_reset_password(
    request: Request,
    admin_id: int,
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)
    if redir := _superadmin_check(owner):
        return redir

    target = (await db.execute(select(AdminUser).where(AdminUser.id == admin_id))).scalar_one_or_none()
    if not target:
        return _flash("/owner/admins", "Администратор не найден", "error")

    plain = generate_password()
    target.password_hash = hash_password(plain)
    await db.commit()
    return _flash("/owner/admins", f"Пароль сброшен. Новый пароль: {plain}")


@router.post("/admins/{admin_id}/toggle-active")
async def admin_toggle_active(
    request: Request,
    admin_id: int,
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)
    if redir := _superadmin_check(owner):
        return redir
    if admin_id == owner.id:
        return _flash("/owner/admins", "Нельзя деактивировать свой аккаунт", "error")

    target = (await db.execute(select(AdminUser).where(AdminUser.id == admin_id))).scalar_one_or_none()
    if not target:
        return _flash("/owner/admins", "Администратор не найден", "error")

    target.is_active = not target.is_active
    await db.commit()
    status = "активирован" if target.is_active else "деактивирован"
    return _flash("/owner/admins", f"Администратор {status}")


@router.post("/admins/{admin_id}/delete")
async def admin_delete(
    request: Request,
    admin_id: int,
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)
    if redir := _superadmin_check(owner):
        return redir
    if admin_id == owner.id:
        return _flash("/owner/admins", "Нельзя удалить свой аккаунт", "error")

    target = (await db.execute(select(AdminUser).where(AdminUser.id == admin_id))).scalar_one_or_none()
    if not target:
        return _flash("/owner/admins", "Администратор не найден", "error")

    await db.delete(target)
    await db.commit()
    return _flash("/owner/admins", "Администратор удалён")


# ── профиль ───────────────────────────────────────────────────────────────────

@router.get("/profile", response_class=HTMLResponse)
async def profile_get(request: Request, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)
    ctx   = await _ctx(request, owner, db)
    return templates.TemplateResponse("owner/profile.html", ctx)


@router.post("/profile")
async def profile_post(
    request:          Request,
    current_password: str = Form(...),
    new_password:     str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    owner  = await require_owner(request, db)
    errors = []

    if not verify_password(current_password, owner.password_hash):
        errors.append("Неверный текущий пароль")
    if new_password != confirm_password:
        errors.append("Новые пароли не совпадают")
    elif pw_errs := validate_password(new_password):
        errors.extend(pw_errs)

    if errors:
        return _flash("/owner/profile", "; ".join(errors), "error")

    owner.password_hash = hash_password(new_password)
    await db.commit()
    return _flash("/owner/profile", "Пароль изменён")


# ── обратная связь ────────────────────────────────────────────────────────────

@router.get("/feedback", response_class=HTMLResponse)
async def feedback_list(
    request:   Request,
    status:    str = "",
    date_from: str = "",
    date_to:   str = "",
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)

    q = select(Feedback).order_by(desc(Feedback.created_at))
    if status:
        q = q.where(Feedback.status == status)
    if date_from:
        q = q.where(Feedback.created_at >= dt.datetime.fromisoformat(date_from))
    if date_to:
        q = q.where(Feedback.created_at <= dt.datetime.fromisoformat(date_to + "T23:59:59"))

    items = (await db.execute(q)).scalars().all()
    ctx   = await _ctx(
        request, owner, db,
        items=items, filter_status=status,
        date_from=date_from, date_to=date_to,
    )
    return templates.TemplateResponse("owner/feedback_list.html", ctx)


@router.get("/feedback/{feedback_id}", response_class=HTMLResponse)
async def feedback_detail(request: Request, feedback_id: int, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)
    fb    = (await db.execute(
        select(Feedback)
        .options(selectinload(Feedback.messages))
        .where(Feedback.id == feedback_id)
    )).scalar_one_or_none()
    if not fb:
        raise HTTPException(404)

    if fb.status == "new":
        fb.status = "read"
        await db.commit()

    ctx = await _ctx(request, owner, db, fb=fb)
    return templates.TemplateResponse("owner/feedback_detail.html", ctx)


@router.post("/feedback/{feedback_id}/reply")
async def feedback_reply(
    request:     Request,
    feedback_id: int,
    message:     str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)
    fb = (await db.execute(select(Feedback).where(Feedback.id == feedback_id))).scalar_one_or_none()
    if not fb:
        return _flash("/owner/feedback", "Запись не найдена", "error")

    if not message.strip():
        return _flash(f"/owner/feedback/{feedback_id}", "Текст ответа не может быть пустым", "error")

    db.add(FeedbackMessage(
        feedback_id=fb.id,
        sender_type="admin",
        sender_id=owner.id,
        sender_name=owner.email,
        message=message.strip(),
    ))
    fb.status = "answered"
    await db.commit()

    if fb.contact_email:
        from app.email import notify_feedback_reply_to_org
        notify_feedback_reply_to_org(
            to=fb.contact_email,
            org_name=fb.org_name or "",
            subject=fb.subject,
            reply_text=message.strip(),
            thread_url=f"/org/feedback/{feedback_id}",
        )

    return _flash(f"/owner/feedback/{feedback_id}", "Ответ отправлен")


@router.post("/feedback/{feedback_id}/update")
async def feedback_update(
    request:     Request,
    feedback_id: int,
    status:      str = Form(...),
    admin_note:  str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    fb = (await db.execute(select(Feedback).where(Feedback.id == feedback_id))).scalar_one_or_none()
    if not fb:
        return _flash("/owner/feedback", "Запись не найдена", "error")

    if status in ("new", "read", "answered"):
        fb.status = status
    fb.admin_note = admin_note or None
    await db.commit()
    return _flash(f"/owner/feedback/{feedback_id}", "Обновлено")


# ── журнал аудита (только superadmin) ────────────────────────────────────────

_PER_PAGE = 50


@router.get("/logs", response_class=HTMLResponse)
async def logs_list(
    request:    Request,
    actor_type: str = "",
    action:     str = "",
    date_from:  str = "",
    date_to:    str = "",
    success:    str = "",
    page:       int = 0,
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)
    if redir := _superadmin_check(owner):
        return redir

    q = _build_log_query(actor_type, action, date_from, date_to, success)
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (await db.execute(q.offset(page * _PER_PAGE).limit(_PER_PAGE))).scalars().all()

    ctx = await _ctx(
        request, owner, db,
        items=items, total=total, page=page, per_page=_PER_PAGE,
        actor_type=actor_type, action=action,
        date_from=date_from, date_to=date_to, success=success,
    )
    return templates.TemplateResponse("owner/logs.html", ctx)


@router.get("/logs/export")
async def logs_export(
    request:    Request,
    actor_type: str = "",
    action:     str = "",
    date_from:  str = "",
    date_to:    str = "",
    success:    str = "",
    db: AsyncSession = Depends(get_session),
):
    owner = await require_owner(request, db)
    if redir := _superadmin_check(owner):
        return redir

    q     = _build_log_query(actor_type, action, date_from, date_to, success)
    items = (await db.execute(q)).scalars().all()

    buf    = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "at", "actor_type", "actor_login", "ip_address",
                     "action", "entity_type", "entity_id", "details", "success"])
    for item in items:
        writer.writerow([
            item.id, item.at, item.actor_type, item.actor_login,
            item.ip_address, item.action, item.entity_type,
            item.entity_id, item.details, item.success,
        ])

    buf.seek(0)
    ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=audit_{ts}.csv"},
    )


def _build_log_query(actor_type: str, action: str, date_from: str, date_to: str, success: str):
    q = select(AuditLog).order_by(desc(AuditLog.at))
    if actor_type:
        q = q.where(AuditLog.actor_type == actor_type)
    if action:
        q = q.where(AuditLog.action == action)
    if date_from:
        q = q.where(AuditLog.at >= dt.datetime.fromisoformat(date_from))
    if date_to:
        q = q.where(AuditLog.at <= dt.datetime.fromisoformat(date_to + "T23:59:59"))
    if success == "true":
        q = q.where(AuditLog.success == True)   # noqa: E712
    elif success == "false":
        q = q.where(AuditLog.success == False)  # noqa: E712
    return q


# ── резервные копии ───────────────────────────────────────────────────────────

def _list_backups() -> list[dict]:
    files = sorted(BACKUP_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {"name": p.name, "size": p.stat().st_size,
         "mtime": dt.datetime.fromtimestamp(p.stat().st_mtime)}
        for p in files
    ]


@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)
    return templates.TemplateResponse(
        "owner/backup.html",
        await _ctx(request, owner, db, backups=_list_backups()),
    )


@router.post("/backup/create")
async def backup_create(request: Request, db: AsyncSession = Depends(get_session)):
    await require_owner(request, db)
    from app.services.backup import create_backup
    data = await create_backup(db)
    ts   = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    (BACKUP_DIR / f"backup_{ts}.json").write_bytes(data)
    return _flash("/owner/backup", "Резервная копия создана")


@router.get("/backup/download/{filename}")
async def backup_download(filename: str, request: Request, db: AsyncSession = Depends(get_session)):
    await require_owner(request, db)
    if not _SAFE_NAME.match(filename):
        raise HTTPException(400, "Недопустимое имя файла")
    path = BACKUP_DIR / filename
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/json", filename=filename)


@router.post("/backup/upload")
async def backup_upload(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
):
    await require_owner(request, db)
    original = Path(file.filename).name if file.filename else "backup.json"
    safe     = re.sub(r'[^\w\-. ]', '_', original)
    if not safe.endswith(".json"):
        safe += ".json"
    (BACKUP_DIR / safe).write_bytes(await file.read())
    return _flash("/owner/backup", f"Файл «{safe}» загружен")


@router.post("/backup/restore/{filename}")
async def backup_restore(filename: str, request: Request, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)
    if not _SAFE_NAME.match(filename):
        raise HTTPException(400, "Недопустимое имя файла")
    path = BACKUP_DIR / filename
    if not path.exists():
        raise HTTPException(404)
    from app.db import engine
    from app.services.backup import restore_backup
    try:
        stats = await restore_backup(engine, path.read_bytes())
    except Exception as exc:
        return templates.TemplateResponse(
            "owner/backup.html",
            await _ctx(request, owner, db, backups=_list_backups(), error=str(exc)),
            status_code=400,
        )
    return templates.TemplateResponse(
        "owner/backup.html",
        await _ctx(request, owner, db,
                   backups=_list_backups(), restored=filename, restore_stats=stats),
    )


@router.post("/backup/delete/{filename}")
async def backup_delete(filename: str, request: Request, db: AsyncSession = Depends(get_session)):
    await require_owner(request, db)
    if not _SAFE_NAME.match(filename):
        raise HTTPException(400, "Недопустимое имя файла")
    path = BACKUP_DIR / filename
    if path.exists():
        path.unlink()
    return _flash("/owner/backup", f"Файл «{filename}» удалён")


# ── утилиты ───────────────────────────────────────────────────────────────────

@router.get("/internal/generate-password")
async def api_generate_password(request: Request, db: AsyncSession = Depends(get_session)):
    await require_owner(request, db)
    return JSONResponse({"password": generate_password()})
