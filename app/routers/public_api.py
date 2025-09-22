import datetime as dt
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import License, LicenseAction, LicenseKey, Client
from app.utils import generate_license_key  # <-- нужен для transfer

router = APIRouter(tags=["public"])  # в main.py включен с prefix="/api"

# --------- Schemas ---------
class ActivationRequest(BaseModel):
    key: str
    device_id: str
    payload: str | None = None

class TransferRequest(BaseModel):
    key: str
    device_id: str

# --------- Helpers ---------
def _expires_value(expires_at: dt.datetime | None) -> str | None:
    # по требованию: "дата окончания, либо бессрочно"
    return expires_at.isoformat() if expires_at else "permanent"

def _license_info_payload(lic: License, client: Client | None) -> dict:
    return {
        "organization": (client.org_name if client else None),
        "description": lic.description,
        "activated_at": lic.activated_at.isoformat() if lic.activated_at else None,
        "expires_at": _expires_value(lic.expires_at),
        "version": lic.version,
    }

# --------- Endpoints ---------
@router.post("/activate")
async def activate(data: ActivationRequest, db: AsyncSession = Depends(get_session)):
    lic = (await db.execute(select(License).where(License.key == data.key))).scalar_one_or_none()
    if not lic:
        return JSONResponse(status_code=404, content={"status": "error", "reason": "Не верная лицензия"})

    client = await db.get(Client, lic.client_id)

    # блокировка
    if lic.is_blocked:
        return JSONResponse(
            status_code=403,
            content={"status": "error", "reason": f"Лицензия заблокирована: {lic.block_reason or ''}".strip(), **_license_info_payload(lic, client)}
        )

    # срок
    if lic.expires_at and dt.datetime.utcnow() > lic.expires_at:
        return JSONResponse(
            status_code=403,
            content={"status": "error", "reason": "Срок действия лицензии закончился", **_license_info_payload(lic, client)}
        )

    # уже активирован (на том же устройстве можно просто подтвердить статус; на другом — запрет)
    if lic.activated_at:
        if lic.device_id == data.device_id:
            # повторная активация тем же устройством — отдадим OK и всю информацию
            return {"status": "ok", **_license_info_payload(lic, client)}
        else:
            return JSONResponse(
                status_code=409,
                content={"status": "error", "reason": "Лицензия была активирована на другом устройстве", **_license_info_payload(lic, client)}
            )

    # активация
    lic.activated_at = dt.datetime.utcnow()
    lic.device_id = data.device_id
    lic.activation_payload = data.payload
    db.add(LicenseAction(license_id=lic.id, action="activate"))
    await db.commit()
    await db.refresh(lic)

    return {"status": "ok", **_license_info_payload(lic, client)}


@router.post("/transfer")
async def transfer_license(data: TransferRequest, db: AsyncSession = Depends(get_session)):
    lic = (await db.execute(select(License).where(License.key == data.key))).scalar_one_or_none()
    if not lic:
        return JSONResponse(status_code=404, content={"status": "error", "reason": "Не верная лицензия"})

    client = await db.get(Client, lic.client_id)

    if lic.is_blocked:
        return JSONResponse(
            status_code=403,
            content={"status": "error", "reason": f"Лицензия заблокирована: {lic.block_reason or ''}".strip(), **_license_info_payload(lic, client)}
        )
    if not lic.activated_at:
        return JSONResponse(
            status_code=409,
            content={"status": "error", "reason": "Лицензия не активирована", **_license_info_payload(lic, client)}
        )
    if lic.device_id != data.device_id:
        return JSONResponse(
            status_code=409,
            content={"status": "error", "reason": "Лицензия была активирована на другом устройстве", **_license_info_payload(lic, client)}
        )
    if lic.expires_at and dt.datetime.utcnow() > lic.expires_at:
        return JSONResponse(
            status_code=403,
            content={"status": "error", "reason": "Срок действия лицензии закончился", **_license_info_payload(lic, client)}
        )

    # деактивируем текущий ключ в истории
    active_key = (await db.execute(
        select(LicenseKey).where(LicenseKey.license_id == lic.id, LicenseKey.is_active == True)
    )).scalars().first()
    if active_key:
        active_key.is_active = False
        active_key.deactivated_at = dt.datetime.utcnow()
        active_key.reason = "transferred"

    # создаём новый ключ (не активирован) в рамках той же лицензии
    new_key = generate_license_key()
    lic.version = (lic.version or 1) + 1
    lic.key = new_key
    lic.activated_at = None
    lic.device_id = None
    lic.activation_payload = None

    db.add(LicenseKey(license_id=lic.id, key=new_key, is_active=True))
    db.add(LicenseAction(license_id=lic.id, action="reset", reason="transfer"))
    await db.commit()
    await db.refresh(lic)

    # в ответ отдаём новый ключ + инфо о лицензии/клиенте
    payload = _license_info_payload(lic, client)
    return {
        "status": "ok",
        "new_key": new_key,
        **payload
    }
