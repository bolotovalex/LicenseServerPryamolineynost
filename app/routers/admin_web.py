import datetime as dt
from typing import Optional
from fastapi import APIRouter, Depends, Request, Form, HTTPException, Response
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.models import AdminUser, Client, License, LicenseAction, LicenseKey
from app.security import hash_password, verify_password, create_access_token, read_token_from_request
from app.utils import generate_license_key, make_qr_png
from fastapi import APIRouter
from sqlalchemy.orm import selectinload


router = APIRouter()
templates = Jinja2Templates(directory="templates")

# ---------- auth helpers ----------
async def get_current_admin(request: Request, db: AsyncSession) -> Optional[AdminUser]:
    payload = read_token_from_request(request)
    if not payload: return None
    user = (await db.execute(select(AdminUser).where(AdminUser.email == payload.get("sub")))).scalar_one_or_none()
    return user if (user and user.is_active) else None

def require_admin(admin: Optional[AdminUser]) -> None:
    if not admin:
        raise HTTPException(status_code=401, detail="Unauthorized")

# ---------- routes ----------

@router.get("/", response_class=HTMLResponse)
async def root(request: Request, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db)
    if not admin: 
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/dashboard", status_code=302)

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@router.post("/login")
async def login_post(request: Request, email: str = Form(...), password: str = Form(...), db: AsyncSession = Depends(get_session)):
    user = (await db.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Неверная почта или пароль"}, status_code=401)
    token = create_access_token(sub=user.email)
    resp = RedirectResponse(url="/dashboard", status_code=303)
    # HttpOnly cookie
    resp.set_cookie("access_token", token, httponly=True, samesite="lax")
    return resp

@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("access_token")
    return resp

# одноразовая инициализация первого админа, если таблица пуста
@router.get("/init-admin", response_class=HTMLResponse)
async def init_admin_get(request: Request, db: AsyncSession = Depends(get_session)):
    count = (await db.execute(select(AdminUser))).scalars().all()
    if count:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "show_init": True, "error": None})

@router.post("/init-admin")
async def init_admin_post(request: Request, email: str = Form(...), password: str = Form(...), db: AsyncSession = Depends(get_session)):
    exist = (await db.execute(select(AdminUser))).scalars().first()
    if exist:
        return RedirectResponse(url="/login", status_code=302)
    user = AdminUser(email=email, password_hash=hash_password(password))
    db.add(user); await db.commit()
    token = create_access_token(sub=user.email)
    resp = RedirectResponse(url="/dashboard", status_code=303)
    resp.set_cookie("access_token", token, httponly=True, samesite="lax")
    return resp

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db); require_admin(admin)

    stmt = (
        select(
            Client.id,
            Client.org_name,
            Client.notes,
            func.count(License.id).label("keys_count"),
        )
        .outerjoin(License, License.client_id == Client.id)
        .group_by(Client.id)
        .order_by(Client.id)
    )
    rows = (await db.execute(stmt)).all()

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "admin": admin, "rows": rows},
    )

# --- Клиенты ---
@router.get("/clients", response_class=HTMLResponse)
async def clients_list(request: Request, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db); require_admin(admin)
    clients = (await db.execute(select(Client).order_by(Client.id))).scalars().all()
    return templates.TemplateResponse("clients.html", {"request": request, "admin": admin, "clients": clients})

@router.post("/clients/new")
async def clients_new(
    request: Request,
    org_name: str = Form(...),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db); require_admin(admin)
    c = Client(org_name=org_name, notes=(notes or None))
    db.add(c); await db.commit()
    return RedirectResponse(url="/clients", status_code=303)


@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(request: Request, client_id: int, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db); require_admin(admin)

    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)

    licenses = (await db.execute(
        select(License)
        .where(License.client_id == client_id)
        .options(selectinload(License.keys))   # загружаем историю ключей
        .order_by(desc(License.issued_at))
    )).scalars().all()

    # сериализуемая «история ключей» для каждой лицензии
    keys_payloads = {
        lic.id: [
            {
                "key": k.key,
                "active": bool(k.is_active),
                "issued_at": k.issued_at.isoformat() if k.issued_at else None,
                "deactivated_at": k.deactivated_at.isoformat() if k.deactivated_at else None,
            }
            for k in lic.keys
        ]
        for lic in licenses
    }

    return templates.TemplateResponse(
        "client_detail.html",
        {
            "request": request,
            "admin": admin,
            "client": client,
            "licenses": licenses,
            "keys_payloads": keys_payloads,  # ← ВАЖНО
        },
    )

# --- Выпуск/сброс/блок ---
@router.post("/licenses/issue")
async def license_issue(
    request: Request,
    client_id: int = Form(...),
    description: str = Form(...),   # <-- новое обязательное поле
    expires_at: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db); require_admin(admin)

    res = await db.execute(
        select(func.max(License.version)).where(License.client_id == client_id)
    )
    max_ver = res.scalar() or 0
    next_ver = max_ver + 1

    exp = dt.datetime.fromisoformat(expires_at) if expires_at else None
    key = generate_license_key()

    lic = License(
        client_id=client_id,
        version=1,
        key=key,
        expires_at=exp,
        description=description,
    )
    db.add(lic)
    await db.flush()  # получим lic.id

    # >>> история ключей: активная запись
    db.add(LicenseKey(license_id=lic.id, key=key, is_active=True))

    db.add(LicenseAction(license_id=lic.id, action="issue"))
    await db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/licenses/{license_id}/reset")
async def license_reset(
    request: Request,
    license_id: int,
    reason: str = Form(...),
    expires_at: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db); require_admin(admin)

    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404, "License not found")

    # 1) деактивировать текущий ключ в истории (если есть)
    active_key = (await db.execute(
        select(LicenseKey).where(
            LicenseKey.license_id == lic.id,
            LicenseKey.is_active == True
        )
    )).scalars().first()
    if active_key:
        active_key.is_active = False
        active_key.deactivated_at = dt.datetime.utcnow()
        active_key.reason = reason

    # 2) сгенерировать новый ключ и ОБНОВИТЬ текущую лицензию
    new_key = generate_license_key()
    lic.version = (lic.version or 1) + 1
    lic.key = new_key
    lic.activated_at = None
    lic.device_id = None
    lic.activation_payload = None
    if expires_at:
        lic.expires_at = dt.datetime.fromisoformat(expires_at)
    # ВАЖНО: description не трогаем (оно обязательно и уже заполнено при первом выпуске)

    # 3) записать новую активную запись в историю ключей
    db.add(LicenseKey(license_id=lic.id, key=new_key, is_active=True))

    # 4) журнал
    db.add(LicenseAction(license_id=lic.id, action="reset", reason=reason))

    await db.commit()
    return RedirectResponse(url=f"/clients/{lic.client_id}", status_code=303)

@router.post("/licenses/{license_id}/block")
async def license_block(request: Request, license_id: int, reason: str = Form(...)):
    db: AsyncSession = await anext(get_session())
    admin = await get_current_admin(request, db); require_admin(admin)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic: raise HTTPException(404)
    lic.is_blocked = True; lic.block_reason = reason
    db.add(LicenseAction(license_id=lic.id, action="block", reason=reason))
    await db.commit()
    return RedirectResponse(url=f"/clients/{lic.client_id}", status_code=303)

@router.post("/licenses/{license_id}/unblock")
async def license_unblock(request: Request, license_id: int):
    db: AsyncSession = await anext(get_session())
    admin = await get_current_admin(request, db); require_admin(admin)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic: raise HTTPException(404)
    lic.is_blocked = False; lic.block_reason = None
    db.add(LicenseAction(license_id=lic.id, action="unblock"))
    await db.commit()
    return RedirectResponse(url=f"/clients/{lic.client_id}", status_code=303)

@router.get("/licenses/{license_id}/qrcode")
async def license_qr(request: Request, license_id: int, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db); require_admin(admin)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic: raise HTTPException(404)
    png = make_qr_png(lic.key)
    return Response(content=png, media_type="image/png")

@router.post("/clients/{client_id}/update-notes")
async def client_update_notes(
    request: Request,
    client_id: int,
    notes: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db); require_admin(admin)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client: 
        raise HTTPException(404)
    client.notes = (notes or None)
    await db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)

@router.post("/licenses/{license_id}/reset")
async def license_reset(
    request: Request,
    license_id: int,
    reason: str = Form(...),
    expires_at: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db); require_admin(admin)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404)

    # деактивируем текущий ключ в истории
    active_key = (await db.execute(
        select(LicenseKey).where(LicenseKey.license_id == lic.id, LicenseKey.is_active == True)
    )).scalars().first()
    if active_key:
        active_key.is_active = False
        active_key.deactivated_at = dt.datetime.utcnow()
        active_key.reason = reason

    # генерируем новый ключ и повышаем версию
    new_key = generate_license_key()
    lic.version = (lic.version or 1) + 1
    lic.key = new_key
    lic.activated_at = None
    lic.device_id = None
    lic.activation_payload = None
    if expires_at:
        lic.expires_at = dt.datetime.fromisoformat(expires_at)

    db.add(LicenseKey(license_id=lic.id, key=new_key, is_active=True))
    db.add(LicenseAction(license_id=lic.id, action="reset", reason=reason))
    await db.commit()
    return RedirectResponse(url=f"/clients/{lic.client_id}", status_code=303)

@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(request: Request, client_id: int, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db); require_admin(admin)

    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)

    licenses = (await db.execute(
        select(License)
        .where(License.client_id == client_id)
        .options(selectinload(License.keys))
        .order_by(desc(License.issued_at))
    )).scalars().all()

    # Готовим сериализуемые данные по ключам для каждой лицензии
    keys_payloads = {
        lic.id: [
            {
                "key": k.key,
                "active": bool(k.is_active),
                "issued_at": k.issued_at.isoformat() if k.issued_at else None,
                "deactivated_at": k.deactivated_at.isoformat() if k.deactivated_at else None,
            }
            for k in lic.keys
        ]
        for lic in licenses
    }

    return templates.TemplateResponse(
        "client_detail.html",
        {
            "request": request,
            "admin": admin,
            "client": client,
            "licenses": licenses,
            "keys_payloads": keys_payloads,  # <-- передаём в шаблон
        },
    )