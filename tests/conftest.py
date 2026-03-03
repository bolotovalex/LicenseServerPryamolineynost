"""
Общие fixtures для тестов.
Используем in-memory SQLite + httpx.AsyncClient.
API signing и email-отправка отключены через dependency overrides.
"""
import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api_signing import verify_api_signature
from app.db import Base, get_session
from app.main import app
from app.models import Client, License, LicenseKey, LicenseAction
from app.security import hash_password
from app.utils import generate_license_key

# ── in-memory SQLite ──────────────────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite://"


@pytest.fixture(scope="session")
def event_loop():
    """Единый event loop на всю сессию."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def api_client(db_engine):
    """
    AsyncClient подключённый к FastAPI-приложению.
    Зависимости get_session и verify_api_signature переопределены:
    - get_session → in-memory SQLite
    - verify_api_signature → no-op (подпись не проверяется)
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_session():
        async with factory() as session:
            yield session

    async def _skip_signature():
        pass  # подпись не нужна в тестах

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[verify_api_signature] = _skip_signature

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


# ── helpers ───────────────────────────────────────────────────────────────────

async def make_client_with_license(
    db: AsyncSession,
    *,
    org_name: str = "Test Org",
    login: str = "testorg",
    password: str = "Testpass1!",
    contact_email: str | None = None,
    max_keys: int = 5,
    key_ttl_days: int | None = None,
    is_blocked: bool = False,
    expires_at=None,
    description: str = "тест",
):
    """Создаёт клиента + 1 лицензию, возвращает (client, license)."""
    org = Client(
        org_name=org_name,
        login=login,
        password_hash=hash_password(password),
        contact_email=contact_email,
        is_active=True,
        max_keys=max_keys,
        key_ttl_days=key_ttl_days,
    )
    db.add(org)
    await db.flush()

    key = generate_license_key()
    lic = License(
        client_id=org.id,
        version=1,
        key=key,
        description=description,
        is_blocked=is_blocked,
        expires_at=expires_at,
    )
    db.add(lic)
    await db.flush()

    db.add(LicenseKey(license_id=lic.id, key=key, is_active=True))
    db.add(LicenseAction(license_id=lic.id, action="issue"))
    await db.commit()
    await db.refresh(lic)
    return org, lic
