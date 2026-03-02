from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.db import engine, Base
from app.routers import public_api, auth, owner_web
from app.models import License, LicenseKey

app = FastAPI(title="License Server")


async def _add_column_if_missing(conn, table: str, column: str, col_def: str) -> None:
    """Добавляет колонку в таблицу только если её ещё нет (SQLite PRAGMA)."""
    rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
    existing = {row[1] for row in rows}
    if column not in existing:
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))


@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
            # admin_users: новые поля
            ("admin_users", "role",            "VARCHAR(32) DEFAULT 'admin'"),
            ("admin_users", "created_by",      "INTEGER REFERENCES admin_users(id)"),
            ("admin_users", "created_at",      "DATETIME"),
            ("admin_users", "last_login_at",   "DATETIME"),
            ("admin_users", "failed_attempts", "INTEGER DEFAULT 0"),
            ("admin_users", "locked_until",    "DATETIME"),
        ]
        for table, column, col_def in migrations:
            await _add_column_if_missing(conn, table, column, col_def)

    # backfill истории ключей
    async with AsyncSession(engine) as s:
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

        from app.services.settings_db import sync_from_config
        await sync_from_config(s)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(public_api.router, prefix="/api")
app.include_router(auth.router)
app.include_router(owner_web.router, prefix="/owner")
