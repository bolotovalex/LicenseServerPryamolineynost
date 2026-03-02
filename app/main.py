from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from app.db import engine, Base
from app.routers import public_api, admin_web
from app.models import License, LicenseKey

app = FastAPI(title="License Server")

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # --- авто-миграции для существующих БД ---
        _migrations = [
            # логотип теперь в БД (blob), не на диске
            "ALTER TABLE clients ADD COLUMN logo_data BLOB",
            "ALTER TABLE clients ADD COLUMN logo_mime VARCHAR(50)",
            # устаревшая колонка из предыдущей версии — оставляем, не удаляем
            # "ALTER TABLE clients DROP COLUMN logo_filename",
        ]
        for sql in _migrations:
            try:
                await conn.execute(text(sql))
            except Exception:
                pass  # колонка уже существует

    # backfill истории ключей: для существующих лицензий создать запись активного ключа
    async with AsyncSession(engine) as s:
        licenses = (await s.execute(select(License))).scalars().all()
        need_commit = False
        for lic in licenses:
            if not lic.key:
                continue
            exists = (await s.execute(select(LicenseKey).where(LicenseKey.key == lic.key))).scalar_one_or_none()
            if not exists:
                s.add(LicenseKey(license_id=lic.id, key=lic.key, is_active=True))
                need_commit = True
        if need_commit:
            await s.commit()

        # синхронизировать настройки из config/*.cfg → app_settings (только новые ключи)
        from app.services.settings_db import sync_from_config
        await sync_from_config(s)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(public_api.router, prefix="/api")
app.include_router(admin_web.router)
