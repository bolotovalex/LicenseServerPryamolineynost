import datetime as dt
import io
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from PIL import Image
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

BACKUP_DIR = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)
_SAFE_NAME = re.compile(r'^[\w\-. ]+\.json$')   # только безопасные имена файлов

from app.db import get_session
from app.models import AdminUser, Client, License, LicenseAction, LicenseKey
from app.security import create_access_token, hash_password, read_token_from_request, verify_password
from app.utils import generate_license_key, make_qr_png

LOGO_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
LOGO_MAX_BYTES = 2 * 1024 * 1024   # 2 МБ
LOGO_MAX_DIM = 512                  # px, вписываем в квадрат LOGO_MAX_DIM×LOGO_MAX_DIM

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ---------- auth helpers ----------

async def get_current_admin(request: Request, db: AsyncSession) -> Optional[AdminUser]:
    payload = read_token_from_request(request)
    if not payload:
        return None
    user = (await db.execute(
        select(AdminUser).where(AdminUser.email == payload.get("sub"))
    )).scalar_one_or_none()
    return user if (user and user.is_active) else None


def require_admin(admin: Optional[AdminUser]) -> None:
    if not admin:
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------- auth routes ----------

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
async def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    user = (await db.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Неверная почта или пароль"}, status_code=401
        )
    token = create_access_token(sub=user.email)
    resp = RedirectResponse(url="/dashboard", status_code=303)
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
    if (await db.execute(select(AdminUser))).scalars().first():
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "show_init": True, "error": None})


@router.post("/init-admin")
async def init_admin_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    if (await db.execute(select(AdminUser))).scalars().first():
        return RedirectResponse(url="/login", status_code=302)
    user = AdminUser(email=email, password_hash=hash_password(password))
    db.add(user)
    await db.commit()
    token = create_access_token(sub=user.email)
    resp = RedirectResponse(url="/dashboard", status_code=303)
    resp.set_cookie("access_token", token, httponly=True, samesite="lax")
    return resp


# ---------- dashboard ----------

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db)
    require_admin(admin)

    rows = (await db.execute(
        select(Client.id, Client.org_name, Client.notes, func.count(License.id).label("keys_count"))
        .outerjoin(License, License.client_id == Client.id)
        .group_by(Client.id)
        .order_by(Client.id)
    )).all()

    return templates.TemplateResponse("dashboard.html", {"request": request, "admin": admin, "rows": rows})


# ---------- clients ----------

@router.get("/clients", response_class=HTMLResponse)
async def clients_list(request: Request, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db)
    require_admin(admin)
    clients = (await db.execute(select(Client).order_by(Client.id))).scalars().all()
    return templates.TemplateResponse("clients.html", {"request": request, "admin": admin, "clients": clients})


@router.post("/clients/new")
async def clients_new(
    request: Request,
    org_name: str = Form(...),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db)
    require_admin(admin)
    db.add(Client(org_name=org_name, notes=(notes or None)))
    await db.commit()
    return RedirectResponse(url="/clients", status_code=303)


@router.get("/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(request: Request, client_id: int, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db)
    require_admin(admin)

    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)

    licenses = (await db.execute(
        select(License)
        .where(License.client_id == client_id)
        .options(selectinload(License.keys))
        .order_by(desc(License.issued_at))
    )).scalars().all()

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
            "keys_payloads": keys_payloads,
        },
    )


@router.post("/clients/{client_id}/update-notes")
async def client_update_notes(
    request: Request,
    client_id: int,
    notes: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db)
    require_admin(admin)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)
    client.notes = (notes or None)
    await db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


# ---------- логотип ----------

@router.get("/clients/{client_id}/logo")
async def client_logo(client_id: int, db: AsyncSession = Depends(get_session)):
    """Отдаёт логотип клиента прямо из БД."""
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
    admin = await get_current_admin(request, db)
    require_admin(admin)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)

    from pathlib import Path
    ext = Path(logo.filename).suffix.lower() if logo.filename else ""
    if ext not in LOGO_ALLOWED_EXT:
        raise HTTPException(400, "Недопустимый формат. Разрешены: jpg, png, gif, webp")

    data = await logo.read()
    if len(data) > LOGO_MAX_BYTES:
        raise HTTPException(400, "Файл слишком большой (максимум 2 МБ)")

    # Открываем и ресайзим через Pillow
    img = Image.open(io.BytesIO(data))
    has_alpha = img.mode in ("RGBA", "P", "LA")
    img = img.convert("RGBA" if has_alpha else "RGB")

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
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/delete-logo")
async def client_delete_logo(
    request: Request,
    client_id: int,
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db)
    require_admin(admin)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)
    client.logo_data = None
    client.logo_mime = None
    await db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


@router.post("/clients/{client_id}/update-info")
async def client_update_info(
    request: Request,
    client_id: int,
    notes: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db)
    require_admin(admin)
    client = (await db.execute(select(Client).where(Client.id == client_id))).scalar_one_or_none()
    if not client:
        raise HTTPException(404)
    client.notes = (notes or None)
    await db.commit()
    return RedirectResponse(url=f"/clients/{client_id}", status_code=303)


# ---------- лицензии ----------

@router.post("/licenses/issue")
async def license_issue(
    request: Request,
    client_id: int = Form(...),
    description: str = Form(...),
    expires_at: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db)
    require_admin(admin)

    exp = dt.datetime.fromisoformat(expires_at) if expires_at else None
    key = generate_license_key()
    lic = License(client_id=client_id, version=1, key=key, expires_at=exp, description=description)
    db.add(lic)
    await db.flush()
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
    admin = await get_current_admin(request, db)
    require_admin(admin)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404)

    active_key = (await db.execute(
        select(LicenseKey).where(LicenseKey.license_id == lic.id, LicenseKey.is_active == True)
    )).scalars().first()
    if active_key:
        active_key.is_active = False
        active_key.deactivated_at = dt.datetime.utcnow()
        active_key.reason = reason

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


@router.post("/licenses/{license_id}/block")
async def license_block(
    request: Request,
    license_id: int,
    reason: str = Form(...),
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db)
    require_admin(admin)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404)
    lic.is_blocked = True
    lic.block_reason = reason
    db.add(LicenseAction(license_id=lic.id, action="block", reason=reason))
    await db.commit()
    return RedirectResponse(url=f"/clients/{lic.client_id}", status_code=303)


@router.post("/licenses/{license_id}/unblock")
async def license_unblock(
    request: Request,
    license_id: int,
    db: AsyncSession = Depends(get_session),
):
    admin = await get_current_admin(request, db)
    require_admin(admin)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404)
    lic.is_blocked = False
    lic.block_reason = None
    db.add(LicenseAction(license_id=lic.id, action="unblock"))
    await db.commit()
    return RedirectResponse(url=f"/clients/{lic.client_id}", status_code=303)


@router.get("/licenses/{license_id}/qrcode")
async def license_qr(request: Request, license_id: int, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db)
    require_admin(admin)
    lic = (await db.execute(select(License).where(License.id == license_id))).scalar_one_or_none()
    if not lic:
        raise HTTPException(404)
    return Response(content=make_qr_png(lic.key), media_type="image/png")


# ---------- резервные копии ----------

def _list_backups() -> list[dict]:
    """Возвращает список файлов из backups/ отсортированный по дате (новые первые)."""
    files = sorted(BACKUP_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "mtime": dt.datetime.fromtimestamp(p.stat().st_mtime),
        }
        for p in files
    ]


@router.get("/backup", response_class=HTMLResponse)
async def backup_page(request: Request, db: AsyncSession = Depends(get_session)):
    admin = await get_current_admin(request, db)
    require_admin(admin)
    return templates.TemplateResponse(
        "backup.html",
        {"request": request, "admin": admin, "backups": _list_backups()},
    )


@router.post("/backup/create")
async def backup_create(request: Request, db: AsyncSession = Depends(get_session)):
    """Создаёт новый файл резервной копии в backups/."""
    admin = await get_current_admin(request, db)
    require_admin(admin)
    from app.services.backup import create_backup
    data = await create_backup(db)
    ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{ts}.json"
    (BACKUP_DIR / filename).write_bytes(data)
    return RedirectResponse(url="/backup", status_code=303)


@router.get("/backup/download/{filename}")
async def backup_download(filename: str, request: Request, db: AsyncSession = Depends(get_session)):
    """Скачивает файл резервной копии."""
    admin = await get_current_admin(request, db)
    require_admin(admin)
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
    """Загружает файл резервной копии на сервер (не восстанавливает, только сохраняет)."""
    admin = await get_current_admin(request, db)
    require_admin(admin)
    original = Path(file.filename).name if file.filename else "backup.json"
    # Безопасное имя: оставляем только допустимые символы
    safe = re.sub(r'[^\w\-. ]', '_', original)
    if not safe.endswith(".json"):
        safe += ".json"
    data = await file.read()
    (BACKUP_DIR / safe).write_bytes(data)
    return RedirectResponse(url="/backup", status_code=303)


@router.post("/backup/restore/{filename}")
async def backup_restore(
    filename: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Восстанавливает БД из файла в backups/."""
    admin = await get_current_admin(request, db)
    require_admin(admin)
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
            "backup.html",
            {"request": request, "admin": admin,
             "backups": _list_backups(), "error": str(exc)},
            status_code=400,
        )
    return templates.TemplateResponse(
        "backup.html",
        {"request": request, "admin": admin,
         "backups": _list_backups(), "restored": filename, "restore_stats": stats},
    )


@router.post("/backup/delete/{filename}")
async def backup_delete(
    filename: str,
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    """Удаляет файл резервной копии."""
    admin = await get_current_admin(request, db)
    require_admin(admin)
    if not _SAFE_NAME.match(filename):
        raise HTTPException(400, "Недопустимое имя файла")
    path = BACKUP_DIR / filename
    if path.exists():
        path.unlink()
    return RedirectResponse(url="/backup", status_code=303)
