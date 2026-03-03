"""
Тесты переходов статусов лицензии.
Проверяем computed_status() и что БД отражает корректный статус после каждого действия.
"""
import datetime as dt

import pytest
from sqlalchemy import select

from app.models import License
from tests.conftest import make_client_with_license


@pytest.mark.asyncio
async def test_initial_status_not_activated(db_session):
    _, lic = await make_client_with_license(db_session)
    assert lic.status == "not_activated"
    assert lic.computed_status() == "not_activated"


@pytest.mark.asyncio
async def test_status_activated_after_api_call(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    lic_id = lic.id
    await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-x"})
    db_session.expire_all()
    fresh = (await db_session.execute(select(License).where(License.id == lic_id))).scalar_one()
    assert fresh.status == "activated"
    assert fresh.computed_status() == "activated"


@pytest.mark.asyncio
async def test_status_released_after_deactivate(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    lic_id = lic.id
    await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-x"})
    await api_client.post("/api/deactivate", json={"key": lic.key, "device_id": "dev-x"})
    db_session.expire_all()
    fresh = (await db_session.execute(select(License).where(License.id == lic_id))).scalar_one()
    assert fresh.status == "released"


@pytest.mark.asyncio
async def test_status_expired_computed(db_session):
    """expired вычисляется на лету по expires_at, не хранится в БД."""
    past = dt.datetime.utcnow() - dt.timedelta(days=1)
    _, lic = await make_client_with_license(db_session, expires_at=past)
    assert lic.status == "not_activated"
    assert lic.computed_status() == "expired"


@pytest.mark.asyncio
async def test_blocked_overrides_expired(db_session):
    """blocked имеет приоритет над expired."""
    past = dt.datetime.utcnow() - dt.timedelta(days=1)
    _, lic = await make_client_with_license(db_session, is_blocked=True, expires_at=past)
    assert lic.computed_status() == "blocked"


@pytest.mark.asyncio
async def test_status_not_activated_after_transfer(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    old_key = lic.key
    await api_client.post("/api/activate", json={"key": old_key, "device_id": "dev-x"})
    r = await api_client.post("/api/transfer", json={"key": old_key, "device_id": "dev-x"})
    new_key = r.json()["new_key"]

    # Находим лицензию по новому ключу
    fresh = (await db_session.execute(
        select(License).where(License.key == new_key)
    )).scalar_one()
    assert fresh.status == "not_activated"
    assert fresh.activated_at is None
    assert fresh.device_id is None


@pytest.mark.asyncio
async def test_reactivation_after_release(api_client, db_session):
    """После released лицензия может быть активирована на новом устройстве."""
    _, lic = await make_client_with_license(db_session)
    await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-1"})
    await api_client.post("/api/deactivate", json={"key": lic.key, "device_id": "dev-1"})
    r = await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-2"})
    assert r.status_code == 200
    assert r.json()["status"] == "activated"
    assert r.json()["device_id"] == "dev-2"
