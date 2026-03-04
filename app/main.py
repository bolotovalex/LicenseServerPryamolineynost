import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.db import engine, Base
from app.config import app_config, db_config, security_config
from app.logging_setup import setup_logging
from app.routers import public_api, auth, owner_web, org_web, feedback as feedback_router
from app.models import License, LicenseKey
from app.api_signing import APISignatureError, nonce_store
from app.response_encryption import encrypt_response, ENCRYPTED_CONTENT_TYPE

# Логирование настраивается первым — до создания app
setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="License Server")


@app.exception_handler(APISignatureError)
async def _signature_error_handler(request: Request, exc: APISignatureError) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"status": "error", "reason": exc.reason, "code": exc.code},
    )


# ── middleware: шифрование ответов /api/* ────────────────────────────────────

@app.middleware("http")
async def encrypt_api_responses(request: Request, call_next):
    """
    Шифрует ответы /api/* алгоритмом AES-256-GCM, если включено в конфиге.

    Ключ шифрования производится из API_SECRET + X-Nonce запроса — каждый
    ответ уникален и не может быть переиспользован (replay-защита).
    Ответы на ошибки подписи (401 без X-Nonce) передаются без шифрования.
    """
    response = await call_next(request)

    if not request.url.path.startswith("/api/"):
        return response
    if not security_config.api_encryption_enabled:
        return response

    nonce = request.headers.get("X-Nonce")
    if not nonce:
        return response

    body = b"".join([chunk async for chunk in response.body_iterator])
    encrypted = encrypt_response(body, app_config.api_secret, nonce)

    return JSONResponse(
        content=encrypted,
        status_code=response.status_code,
        media_type=ENCRYPTED_CONTENT_TYPE,
    )


# ── middleware: логирование запросов ─────────────────────────────────────────

@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    if request.url.path.startswith("/static/"):
        return await call_next(request)

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start) * 1000)

    forwarded = request.headers.get("X-Forwarded-For")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else "-"
    )
    ua = request.headers.get("User-Agent", "-")

    logger.info(
        "%s | %s | %s | %s | %dms | %s | %s",
        time.strftime("%Y-%m-%dT%H:%M:%S"),
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        ip,
        ua,
    )
    return response


# ── helpers ───────────────────────────────────────────────────────────────────

async def _add_column_if_missing_sqlite(conn, table: str, column: str, col_def: str) -> None:
    """Добавляет колонку только если её нет (SQLite PRAGMA)."""
    rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
    existing = {row[1] for row in rows}
    if column not in existing:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))


async def _add_column_if_missing_pg(conn, table: str, column: str, col_def: str) -> None:
    """Добавляет колонку через IF NOT EXISTS (PostgreSQL / MariaDB 10.3+)."""
    try:
        await conn.execute(text(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_def}"
        ))
    except Exception:
        pass  # колонка уже существует


async def _run_migrations(conn) -> None:
    """Запускает schema-миграции для существующих БД."""
    db_type = db_config.db_type

    if db_type == "sqlite":
        add_col = _add_column_if_missing_sqlite
    else:
        add_col = _add_column_if_missing_pg

    migrations = [
        # clients: логотип
        ("clients", "logo_data",       "BLOB"),
        ("clients", "logo_mime",       "VARCHAR(50)"),
        # clients: новые поля
        ("clients", "login",           "VARCHAR(64)"),
        ("clients", "password_hash",   "VARCHAR(255)"),
        ("clients", "is_active",       "BOOLEAN DEFAULT 1"),
        ("clients", "max_keys",        "INTEGER DEFAULT 5"),
        ("clients", "key_ttl_days",    "INTEGER"),
        ("clients", "contact_email",   "VARCHAR(255)"),
        ("clients", "created_by",      "INTEGER REFERENCES admin_users(id)"),
        ("clients", "failed_attempts", "INTEGER DEFAULT 0"),
        ("clients", "locked_until",    "DATETIME"),
        ("clients", "last_login_at",   "DATETIME"),
        ("clients", "deleted_at",      "DATETIME"),
        # admin_users: новые поля
        ("admin_users", "role",            "VARCHAR(32) DEFAULT 'admin'"),
        ("admin_users", "created_by",      "INTEGER REFERENCES admin_users(id)"),
        ("admin_users", "created_at",      "DATETIME"),
        ("admin_users", "last_login_at",   "DATETIME"),
        ("admin_users", "failed_attempts", "INTEGER DEFAULT 0"),
        ("admin_users", "locked_until",    "DATETIME"),
        # licenses: явный статус и поля устройства
        ("licenses", "status",         "VARCHAR(20) DEFAULT 'not_activated'"),
        ("licenses", "device_name",    "VARCHAR(255)"),
        ("licenses", "device_comment", "TEXT"),
        # licenses: мягкое удаление
        ("licenses", "deleted_at",     "DATETIME"),
        # license_actions: расширенный аудит
        ("license_actions", "desc",   "TEXT"),
        ("license_actions", "actor",  "VARCHAR(255)"),
        ("license_actions", "ip",     "VARCHAR(45)"),
    ]
    for table, column, col_def in migrations:
        await add_col(conn, table, column, col_def)


async def _backfill_license_status(session: AsyncSession) -> None:
    """Заполняет License.status для существующих записей с status IS NULL или пустым."""
    import datetime as dt
    licenses = (await session.execute(select(License))).scalars().all()
    now = dt.datetime.utcnow()
    need_commit = False
    for lic in licenses:
        if lic.status and lic.status != "not_activated":
            continue
        if lic.is_blocked:
            lic.status = "blocked"
        elif lic.activated_at:
            lic.status = "activated"
        else:
            lic.status = "not_activated"
        need_commit = True
    if need_commit:
        await session.commit()


# ── startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _run_migrations(conn)

    async with AsyncSession(engine) as s:
        # backfill истории ключей (LicenseKey)
        licenses = (await s.execute(select(License))).scalars().all()
        need_commit = False
        for lic in licenses:
            if not lic.key:
                continue
            exists = (await s.execute(
                select(LicenseKey).where(LicenseKey.key == lic.key)
            )).scalar_one_or_none()
            if not exists:
                s.add(LicenseKey(license_id=lic.id, key=lic.key, is_active=True))
                need_commit = True
        if need_commit:
            await s.commit()

        # backfill License.status
        await _backfill_license_status(s)

        from app.services.settings_db import sync_from_config
        await sync_from_config(s)

    nonce_store.start_cleanup()
    logger.info("Приложение запущено (db_type=%s).", db_config.db_type)


# ── routers & static ─────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(public_api.router, prefix="/api")
app.include_router(auth.router)
app.include_router(owner_web.router, prefix="/owner")
app.include_router(org_web.router, prefix="/org")
app.include_router(feedback_router.router)
