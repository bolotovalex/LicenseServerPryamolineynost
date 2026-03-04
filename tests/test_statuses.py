"""
Тесты переходов статусов лицензии.
Проверяем computed_status() и что БД отражает корректный статус после каждого действия.
"""
import datetime as dt

import pytest
from sqlalchemy import select

from app.models import License, LicenseKey
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


@pytest.mark.asyncio
async def test_org_reset_clears_device_id(api_client, db_session):
    """
    После org-сброса:
    - версия увеличилась до 2
    - device_id, device_name, device_comment, activation_payload, activated_at — None
    - статус — not_activated
    - старый LicenseKey деактивирован, новый активен
    """
    org, lic = await make_client_with_license(db_session, login="resetorg", password="Testpass1!")
    lic_id  = lic.id
    old_key = lic.key

    # Активируем через публичный API
    r = await api_client.post("/api/activate", json={
        "key": old_key, "device_id": "dev-reset", "device_name": "Test Device",
    })
    assert r.status_code == 200

    # Деактивируем через публичный API → status = released
    r = await api_client.post("/api/deactivate", json={
        "key": old_key, "device_id": "dev-reset",
    })
    assert r.status_code == 200

    # Логинимся как org, получаем cookie
    r = await api_client.post("/login", data={"login": "resetorg", "password": "Testpass1!"})
    assert r.status_code in (200, 303)

    # Сбрасываем ключ
    r = await api_client.post(f"/org/licenses/{lic_id}/reset")
    assert r.status_code in (200, 303)

    # Перечитываем лицензию из БД
    db_session.expire_all()
    fresh = (await db_session.execute(
        select(License).where(License.id == lic_id)
    )).scalar_one()

    assert fresh.version == 2,              "версия должна быть 2"
    assert fresh.device_id is None,         "device_id должен быть None"
    assert fresh.device_name is None,       "device_name должен быть None"
    assert fresh.device_comment is None,    "device_comment должен быть None"
    assert fresh.activation_payload is None,"activation_payload должен быть None"
    assert fresh.activated_at is None,      "activated_at должен быть None"
    assert fresh.status == "not_activated", "статус должен быть not_activated"
    assert fresh.key != old_key,            "ключ должен смениться"

    # Старый LicenseKey должен быть деактивирован
    old_lk = (await db_session.execute(
        select(LicenseKey).where(LicenseKey.key == old_key)
    )).scalar_one()
    assert not old_lk.is_active, "старый LicenseKey должен быть деактивирован"

    # Новый LicenseKey должен быть активен
    new_lk = (await db_session.execute(
        select(LicenseKey).where(LicenseKey.key == fresh.key)
    )).scalar_one()
    assert new_lk.is_active, "новый LicenseKey должен быть активен"


@pytest.mark.asyncio
async def test_org_reset_blocked_license_forbidden(api_client, db_session):
    """Org не может сбросить заблокированный ключ."""
    org, lic = await make_client_with_license(db_session, login="blockedorg", password="Testpass1!", is_blocked=True)
    lic_id  = lic.id
    old_key = lic.key

    r = await api_client.post("/login", data={"login": "blockedorg", "password": "Testpass1!"})
    assert r.status_code in (200, 303)

    r = await api_client.post(f"/org/licenses/{lic_id}/reset")
    assert r.status_code in (200, 303)

    db_session.expire_all()
    fresh = (await db_session.execute(
        select(License).where(License.id == lic_id)
    )).scalar_one()
    assert fresh.key == old_key, "ключ не должен был смениться"
    assert fresh.version == 1,   "версия не должна была увеличиться"


@pytest.mark.asyncio
async def test_org_reset_not_allowed_for_activated(api_client, db_session):
    """Org не может сбросить активированный ключ."""
    org, lic = await make_client_with_license(db_session, login="activeorg", password="Testpass1!")
    lic_id  = lic.id
    old_key = lic.key

    await api_client.post("/api/activate", json={"key": old_key, "device_id": "dev-x"})

    r = await api_client.post("/login", data={"login": "activeorg", "password": "Testpass1!"})
    assert r.status_code in (200, 303)

    r = await api_client.post(f"/org/licenses/{lic_id}/reset")
    assert r.status_code in (200, 303)

    db_session.expire_all()
    fresh = (await db_session.execute(
        select(License).where(License.id == lic_id)
    )).scalar_one()
    assert fresh.status == "activated", "статус должен остаться activated"
    assert fresh.key == old_key,        "ключ не должен был смениться"
