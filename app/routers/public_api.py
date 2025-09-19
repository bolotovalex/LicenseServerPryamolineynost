import datetime as dt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.models import License, LicenseAction, LicenseKey

router = APIRouter(tags=["public"])

class ActivationRequest(BaseModel):
    key: str
    device_id: str
    payload: str | None = None

@router.post("/activate")
async def activate(data: ActivationRequest, db: AsyncSession = Depends(get_session)):
    lic = (await db.execute(select(License).where(License.key == data.key))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404, "License not found")
    if lic.is_blocked:
        raise HTTPException(403, f"License blocked: {lic.block_reason or ''}".strip())
    if lic.activated_at:
        raise HTTPException(409, "License already activated")
    if lic.expires_at and dt.datetime.utcnow() > lic.expires_at:
        raise HTTPException(403, "License expired")

    lic.activated_at = dt.datetime.utcnow()
    lic.device_id = data.device_id
    lic.activation_payload = data.payload
    db.add(LicenseAction(license_id=lic.id, action="activate"))
    await db.commit()
    return {"status": "ok", "activated_at": lic.activated_at.isoformat(), "version": lic.version, "description": lic.description}

class TransferRequest(BaseModel):
    key: str
    device_id: str

@router.post("/transfer")
async def transfer_license(data: TransferRequest, db: AsyncSession = Depends(get_session)):
    lic = (await db.execute(select(License).where(License.key == data.key))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404, "License not found")
    if lic.is_blocked:
        raise HTTPException(403, f"License blocked: {lic.block_reason or ''}".strip())
    if not lic.activated_at:
        raise HTTPException(409, "License is not activated")
    if lic.device_id != data.device_id:
        raise HTTPException(409, "License activated on another device")
    if lic.expires_at and dt.datetime.utcnow() > lic.expires_at:
        raise HTTPException(403, "License expired")

    # деактивируем текущий ключ в истории
    active_key = (await db.execute(
        select(LicenseKey).where(LicenseKey.license_id == lic.id, LicenseKey.is_active == True)
    )).scalars().first()
    if active_key:
        active_key.is_active = False
        active_key.deactivated_at = dt.datetime.utcnow()
        active_key.reason = "transferred"

    # создаём новый ключ (не активирован)
    new_key = generate_license_key()
    lic.version = (lic.version or 1) + 1
    lic.key = new_key
    lic.activated_at = None
    lic.device_id = None
    lic.activation_payload = None

    db.add(LicenseKey(license_id=lic.id, key=new_key, is_active=True))
    db.add(LicenseAction(license_id=lic.id, action="reset", reason="transfer"))
    await db.commit()

    return {
        "status": "ok",
        "new_key": new_key,
        "new_version": lic.version,
        "expires_at": lic.expires_at.isoformat() if lic.expires_at else None,
        "description": lic.description,
    }
