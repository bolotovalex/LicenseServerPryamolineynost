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
    payload: str | None = None


class TransferRequest(BaseModel):
    key: str
    device_id: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _err(reason: str, code: str, extra: dict | None = None) -> dict:
    """Единый формат ошибки: {"status":"error","reason":"...","code":"..."}."""
    d = {"status": "error", "reason": reason, "code": code}
    if extra:
        d.update(extra)
    return d


def _expires_value(expires_at: dt.datetime | None) -> str | None:
    return expires_at.isoformat() if expires_at else "permanent"


def _license_info_payload(lic: License, client: Client | None) -> dict:
    return {
        "organization": (client.org_name if client else None),
        "description":  lic.description,
        "activated_at": lic.activated_at.isoformat() if lic.activated_at else None,
        "expires_at":   _expires_value(lic.expires_at),
        "version":      lic.version,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/activate")
async def activate(
    request: Request,
    data: ActivationRequest,
    db: AsyncSession = Depends(get_session),
):
    lic = (await db.execute(select(License).where(License.key == data.key))).scalar_one_or_none()
    if not lic:
        return JSONResponse(
            status_code=404,
            content=_err("Лицензия не найдена", "LICENSE_NOT_FOUND"),
        )

    client = await db.get(Client, lic.client_id)
    info = _license_info_payload(lic, client)

    if lic.is_blocked:
        return JSONResponse(
            status_code=403,
            content=_err(
                f"Лицензия заблокирована: {lic.block_reason or ''}".strip(),
                "LICENSE_BLOCKED",
                info,
            ),
        )

    if lic.expires_at and dt.datetime.utcnow() > lic.expires_at:
        return JSONResponse(
            status_code=403,
            content=_err("Срок действия лицензии истёк", "LICENSE_EXPIRED", info),
        )

    if lic.activated_at:
        if lic.device_id == data.device_id:
            # Повторная активация тем же устройством — подтверждаем статус
            return {"status": "ok", **info}
        return JSONResponse(
            status_code=409,
            content=_err("Лицензия уже активирована на другом устройстве", "DEVICE_MISMATCH", info),
        )

    # Активация
    lic.activated_at = dt.datetime.utcnow()
    lic.device_id = data.device_id
    lic.activation_payload = data.payload
    db.add(LicenseAction(license_id=lic.id, action="activate"))
    await log_action(
        db=db,
        actor_type="api_client",
        action="activate",
        actor_login=data.device_id,
        entity_type="license",
        entity_id=lic.id,
        details={"key_prefix": data.key[:8]},
        success=True,
        request=request,
    )
    await db.commit()
    await db.refresh(lic)

    return {"status": "ok", **_license_info_payload(lic, client)}


@router.post("/transfer")
async def transfer_license(
    request: Request,
    data: TransferRequest,
    db: AsyncSession = Depends(get_session),
):
    lic = (await db.execute(select(License).where(License.key == data.key))).scalar_one_or_none()
    if not lic:
        return JSONResponse(
            status_code=404,
            content=_err("Лицензия не найдена", "LICENSE_NOT_FOUND"),
        )

    client = await db.get(Client, lic.client_id)
    info = _license_info_payload(lic, client)

    if lic.is_blocked:
        return JSONResponse(
            status_code=403,
            content=_err(
                f"Лицензия заблокирована: {lic.block_reason or ''}".strip(),
                "LICENSE_BLOCKED",
                info,
            ),
        )

    if not lic.activated_at:
        return JSONResponse(
            status_code=409,
            content=_err("Лицензия не активирована", "NOT_ACTIVATED", info),
        )

    if lic.device_id != data.device_id:
        return JSONResponse(
            status_code=409,
            content=_err("Лицензия была активирована на другом устройстве", "DEVICE_MISMATCH", info),
        )

    if lic.expires_at and dt.datetime.utcnow() > lic.expires_at:
        return JSONResponse(
            status_code=403,
            content=_err("Срок действия лицензии истёк", "LICENSE_EXPIRED", info),
        )

    # Деактивируем текущий ключ в истории
    active_key = (await db.execute(
        select(LicenseKey).where(LicenseKey.license_id == lic.id, LicenseKey.is_active == True)
    )).scalars().first()
    if active_key:
        active_key.is_active = False
        active_key.deactivated_at = dt.datetime.utcnow()
        active_key.reason = "transferred"

    old_key_prefix = lic.key[:8] if lic.key else "?"
    new_key = generate_license_key()
    lic.version = (lic.version or 1) + 1
    lic.key = new_key
    lic.activated_at = None
    lic.device_id = None
    lic.activation_payload = None

    db.add(LicenseKey(license_id=lic.id, key=new_key, is_active=True))
    db.add(LicenseAction(license_id=lic.id, action="reset", reason="transfer"))
    await log_action(
        db=db,
        actor_type="api_client",
        action="transfer",
        actor_login=data.device_id,
        entity_type="license",
        entity_id=lic.id,
        details={"old_key_prefix": old_key_prefix, "new_key_prefix": new_key[:8]},
        success=True,
        request=request,
    )
    await db.commit()
    await db.refresh(lic)

    return {
        "status": "ok",
        "new_key": new_key,
        **_license_info_payload(lic, client),
    }


@router.get("/status")
async def license_status(key: str, db: AsyncSession = Depends(get_session)):
    """Возвращает текущее состояние лицензии без изменения данных."""
    lic = (await db.execute(select(License).where(License.key == key))).scalar_one_or_none()
    if not lic:
        return JSONResponse(
            status_code=404,
            content=_err("Лицензия не найдена", "LICENSE_NOT_FOUND"),
        )

    client = await db.get(Client, lic.client_id)

    return {
        "status": "ok",
        "is_blocked":   lic.is_blocked,
        "block_reason": lic.block_reason if lic.is_blocked else None,
        "is_activated": lic.activated_at is not None,
        **_license_info_payload(lic, client),
    }
