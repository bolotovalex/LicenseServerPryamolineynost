"""
Публичный JSON API для клиентских приложений (prefix=/api).
Все запросы верифицируются HMAC-подписью через verify_api_signature.
"""
import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import License, LicenseAction, LicenseKey, Client
from app.utils import generate_license_key
from app.audit import log_action
from app.api_signing import verify_api_signature

router = APIRouter(
    tags=["public"],
    dependencies=[Depends(verify_api_signature)],
)  # в main.py включен с prefix="/api"


# ── Schemas ───────────────────────────────────────────────────────────────────

class ActivationRequest(BaseModel):
    key: str
    device_id: str
    device_name: str | None = None     # человекочитаемое имя устройства
    comment: str | None = None         # произвольный комментарий от клиента
    activated_at: str | None = None    # дата активации по часам клиента (ISO)
    key_version: int | None = None     # версия ключа, известная клиенту
    payload: str | None = None         # устаревшее поле, оставлено для совместимости


class DeactivateRequest(BaseModel):
    key: str
    device_id: str



class TransferRequest(BaseModel):
    key: str
    device_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    return forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "unknown"
    )


def _err(reason: str, code: str, extra: dict | None = None) -> dict:
    d = {"status": "error", "reason": reason, "code": code}
    if extra:
        d.update(extra)
    return d


def _expires_value(expires_at: dt.datetime | None) -> str | None:
    return expires_at.isoformat() if expires_at else "permanent"


def _license_info(lic: License, client: Client | None, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.utcnow()
    logo_url = f"/owner/clients/{client.id}/logo" if client and client.logo_data else None
    return {
        "license_id":   lic.id,
        "organization": client.org_name if client else None,
        "description":  lic.description,
        "status":       lic.computed_status(now),
        "activated_at": lic.activated_at.isoformat() if lic.activated_at else None,
        "expires_at":   _expires_value(lic.expires_at),
        "version":      lic.version,
        "device_id":    lic.device_id,
        "device_name":  lic.device_name,
        "logo_url":     logo_url,
    }


async def _log_api_error(
    db, request: Request, action: str, code: str,
    device_id: str | None, ip: str,
    lic_id: int | None = None,
) -> None:
    """Логирует ошибку API в LicenseAction (если есть lic_id) и AuditLog."""
    if lic_id:
        db.add(LicenseAction(
            license_id=lic_id,
            action=action,
            reason=code,
            actor=device_id or "unknown",
            ip=ip,
        ))
    await log_action(
        db=db,
        actor_type="api_client",
        action=f"error_{action}",
        actor_login=device_id or "unknown",
        entity_type="license",
        entity_id=lic_id,
        details={"code": code, "device_id": device_id, "ip": ip},
        success=False,
        request=request,
    )
    await db.commit()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/activate")
async def activate(
    request: Request,
    data: ActivationRequest,
    db: AsyncSession = Depends(get_session),
):
    """
    Активация лицензионного ключа на устройстве.
    Идемпотентна для того же device_id (повторный вызов → подтверждение).
    Сценарий 3: если device_id уже привязан к другой лицензии — освобождает её.
    """
    now = dt.datetime.utcnow()
    ip  = _get_ip(request)

    lic = (await db.execute(select(License).where(License.key == data.key))).scalar_one_or_none()
    if not lic:
        await _log_api_error(db, request, "activate", "LICENSE_NOT_FOUND", data.device_id, ip)
        return JSONResponse(status_code=404, content=_err("Лицензия не найдена", "LICENSE_NOT_FOUND"))

    client = await db.get(Client, lic.client_id)

    if lic.is_blocked:
        await _log_api_error(db, request, "activate", "LICENSE_BLOCKED", data.device_id, ip, lic.id)
        return JSONResponse(status_code=403, content=_err(
            f"Лицензия заблокирована: {lic.block_reason or ''}".strip(), "LICENSE_BLOCKED",
            {"reason": lic.block_reason or ""},
        ))

    if lic.expires_at and now > lic.expires_at:
        await _log_api_error(db, request, "activate", "LICENSE_EXPIRED", data.device_id, ip, lic.id)
        return JSONResponse(status_code=403, content=_err(
            "Срок действия лицензии истёк", "LICENSE_EXPIRED",
        ))

    if data.key_version and data.key_version != lic.version:
        await _log_api_error(db, request, "activate", "VERSION_MISMATCH", data.device_id, ip, lic.id)
        return JSONResponse(status_code=409, content=_err(
            "Версия ключа устарела, запросите актуальный ключ", "VERSION_MISMATCH",
        ))

    # Сценарий 2: тот же device_id — подтверждаем, обновляем имя/комментарий
    if lic.status == "activated" and lic.device_id == data.device_id:
        if data.device_name:
            lic.device_name = data.device_name
        if data.comment:
            lic.device_comment = data.comment
        await db.commit()
        await db.refresh(lic)
        return {"status": "ok", **_license_info(lic, client, now)}

    # Сценарий 3: device_id уже привязан к другой активированной лицензии — освобождаем её
    old_lic = (await db.execute(
        select(License).where(
            License.device_id == data.device_id,
            License.status == "activated",
            License.id != lic.id,
        )
    )).scalar_one_or_none()
    if old_lic:
        old_lic.status        = "released"
        old_lic.activated_at  = None
        old_lic.device_id     = None
        old_lic.device_name   = None
        old_lic.device_comment = None
        db.add(LicenseAction(
            license_id=old_lic.id, action="deactivate",
            reason="device switched to new key",
            actor=data.device_id, ip=ip,
        ))
        await log_action(
            db=db, actor_type="api_client", action="device_key_swap",
            actor_login=data.device_id, entity_type="license", entity_id=old_lic.id,
            details={"old_key": old_lic.key[:8], "new_key": data.key[:8]},
            success=True, request=request,
        )

    # Сценарий 4: ключ активирован другим устройством — DEVICE_MISMATCH
    if lic.status == "activated" and lic.device_id != data.device_id:
        await _log_api_error(db, request, "activate", "DEVICE_MISMATCH", data.device_id, ip, lic.id)
        return JSONResponse(status_code=409, content=_err(
            "Лицензия уже активирована на другом устройстве", "DEVICE_MISMATCH",
        ))

    # Сценарий 1: активация
    lic.activated_at       = now
    lic.device_id          = data.device_id
    lic.device_name        = data.device_name
    lic.device_comment     = data.comment
    lic.activation_payload = data.payload
    lic.status             = "activated"

    db.add(LicenseAction(
        license_id=lic.id, action="activate",
        actor=data.device_id, ip=ip,
        desc=f"device_name={data.device_name or '—'}",
    ))
    await log_action(
        db=db, actor_type="api_client", action="activate",
        actor_login=data.device_id, entity_type="license", entity_id=lic.id,
        details={"key_prefix": data.key[:8], "device_name": data.device_name},
        success=True, request=request,
    )
    await db.commit()
    await db.refresh(lic)
    return {"status": "ok", **_license_info(lic, client, now)}


@router.post("/deactivate")
async def deactivate(
    request: Request,
    data: DeactivateRequest,
    db: AsyncSession = Depends(get_session),
):
    """
    Деактивация (освобождение) лицензии устройством.
    После деактивации лицензия переходит в статус released и может быть
    активирована на любом другом устройстве.
    """
    now = dt.datetime.utcnow()
    lic = (await db.execute(select(License).where(License.key == data.key))).scalar_one_or_none()
    if not lic:
        return JSONResponse(status_code=404, content=_err("Лицензия не найдена", "LICENSE_NOT_FOUND"))

    client = await db.get(Client, lic.client_id)
    info = _license_info(lic, client, now)

    if lic.is_blocked:
        return JSONResponse(status_code=403, content=_err(
            "Лицензия заблокирована", "LICENSE_BLOCKED", info
        ))

    if not lic.activated_at:
        return JSONResponse(status_code=409, content=_err(
            "Лицензия не активирована", "NOT_ACTIVATED", info
        ))

    if lic.device_id != data.device_id:
        return JSONResponse(status_code=409, content=_err(
            "Лицензия активирована на другом устройстве", "DEVICE_MISMATCH", info
        ))

    lic.activated_at   = None
    lic.device_id      = None
    lic.device_name    = None
    lic.device_comment = None
    lic.status         = "released"

    db.add(LicenseAction(
        license_id=lic.id, action="deactivate",
        actor=data.device_id, ip=_get_ip(request),
        desc="client-side deactivation",
    ))
    await log_action(
        db=db, actor_type="api_client", action="deactivate",
        actor_login=data.device_id, entity_type="license", entity_id=lic.id,
        details={"key_prefix": data.key[:8]}, success=True, request=request,
    )
    await db.commit()
    return {"status": "ok", "code": "DEACTIVATED", "message": "Лицензия освобождена"}


@router.post("/transfer")
async def transfer_license(
    request: Request,
    data: TransferRequest,
    db: AsyncSession = Depends(get_session),
):
    """
    Перенос лицензии: деактивирует текущий ключ, генерирует новый.
    Вызывается с device_id текущего активного устройства.
    """
    now = dt.datetime.utcnow()
    lic = (await db.execute(select(License).where(License.key == data.key))).scalar_one_or_none()
    if not lic:
        return JSONResponse(status_code=404, content=_err("Лицензия не найдена", "LICENSE_NOT_FOUND"))

    client = await db.get(Client, lic.client_id)
    info = _license_info(lic, client, now)

    if lic.is_blocked:
        return JSONResponse(status_code=403, content=_err(
            f"Лицензия заблокирована: {lic.block_reason or ''}".strip(), "LICENSE_BLOCKED", info
        ))

    if not lic.activated_at:
        return JSONResponse(status_code=409, content=_err(
            "Лицензия не активирована", "NOT_ACTIVATED", info
        ))

    if lic.device_id != data.device_id:
        return JSONResponse(status_code=409, content=_err(
            "Лицензия была активирована на другом устройстве", "DEVICE_MISMATCH", info
        ))

    if lic.expires_at and now > lic.expires_at:
        return JSONResponse(status_code=403, content=_err(
            "Срок действия лицензии истёк", "LICENSE_EXPIRED", info
        ))

    # Деактивируем текущий ключ в истории
    active_key = (await db.execute(
        select(LicenseKey).where(LicenseKey.license_id == lic.id, LicenseKey.is_active == True)
    )).scalars().first()
    if active_key:
        active_key.is_active = False
        active_key.deactivated_at = now
        active_key.reason = "transferred"

    old_key_prefix = lic.key[:8] if lic.key else "?"
    new_key = generate_license_key()
    lic.version        = (lic.version or 1) + 1
    lic.key            = new_key
    lic.activated_at   = None
    lic.device_id      = None
    lic.device_name    = None
    lic.device_comment = None
    lic.activation_payload = None
    lic.status         = "not_activated"

    db.add(LicenseKey(license_id=lic.id, key=new_key, is_active=True))
    db.add(LicenseAction(
        license_id=lic.id, action="reset", reason="transfer",
        actor=data.device_id, ip=_get_ip(request),
    ))
    await log_action(
        db=db, actor_type="api_client", action="transfer",
        actor_login=data.device_id, entity_type="license", entity_id=lic.id,
        details={"old_key_prefix": old_key_prefix, "new_key_prefix": new_key[:8]},
        success=True, request=request,
    )
    await db.commit()
    await db.refresh(lic)
    return {"status": "ok", "new_key": new_key, **_license_info(lic, client)}


@router.get("/status")
async def license_status(key: str, db: AsyncSession = Depends(get_session)):
    """Возвращает текущее состояние лицензии без изменения данных."""
    now = dt.datetime.utcnow()
    lic = (await db.execute(select(License).where(License.key == key))).scalar_one_or_none()
    if not lic:
        return JSONResponse(status_code=404, content=_err("Лицензия не найдена", "LICENSE_NOT_FOUND"))

    client = await db.get(Client, lic.client_id)
    return {"status": "ok", **_license_info(lic, client, now)}


@router.get("/history")
async def license_history(key: str, db: AsyncSession = Depends(get_session)):
    """История ключей и действий для указанной лицензии."""
    lic = (await db.execute(select(License).where(License.key == key))).scalar_one_or_none()
    if not lic:
        return JSONResponse(status_code=404, content=_err("Лицензия не найдена", "LICENSE_NOT_FOUND"))

    await db.refresh(lic, ["keys", "actions"])

    keys_history = [
        {
            "key":            k.key,
            "is_active":      k.is_active,
            "issued_at":      k.issued_at.isoformat() if k.issued_at else None,
            "deactivated_at": k.deactivated_at.isoformat() if k.deactivated_at else None,
            "reason":         k.reason,
        }
        for k in lic.keys
    ]
    actions_history = [
        {
            "action": a.action,
            "at":     a.at.isoformat() if a.at else None,
            "reason": a.reason,
            "actor":  a.actor,
        }
        for a in lic.actions
    ]
    return {
        "status":  "ok",
        "license_id": lic.id,
        "keys":    keys_history,
        "actions": actions_history,
    }
