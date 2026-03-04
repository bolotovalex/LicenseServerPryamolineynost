"""
Тесты публичного API: /api/activate, /api/deactivate, /api/transfer,
/api/status, /api/history.
"""
import datetime as dt

import pytest

from tests.conftest import make_client_with_license


# ── activate ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activate_new_license(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    resp = await api_client.post("/api/activate", json={
        "key": lic.key, "device_id": "dev-001", "device_name": "My PC",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "activated"
    assert data["device_id"] == "dev-001"
    assert data["device_name"] == "My PC"


@pytest.mark.asyncio
async def test_activate_idempotent_same_device(api_client, db_session):
    """Повторная активация тем же device_id → 200 OK."""
    _, lic = await make_client_with_license(db_session)
    payload = {"key": lic.key, "device_id": "dev-001"}
    r1 = await api_client.post("/api/activate", json=payload)
    r2 = await api_client.post("/api/activate", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_activate_device_mismatch(api_client, db_session):
    """Активация другим device_id → 409 DEVICE_MISMATCH."""
    _, lic = await make_client_with_license(db_session)
    await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-001"})
    resp = await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-002"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "DEVICE_MISMATCH"


@pytest.mark.asyncio
async def test_activate_not_found(api_client, db_session):
    resp = await api_client.post("/api/activate", json={"key": "XXXX-XXXX-XXXX", "device_id": "d"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "LICENSE_NOT_FOUND"


@pytest.mark.asyncio
async def test_activate_blocked_license(api_client, db_session):
    _, lic = await make_client_with_license(db_session, is_blocked=True)
    resp = await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-001"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "LICENSE_BLOCKED"


@pytest.mark.asyncio
async def test_activate_expired_license(api_client, db_session):
    past = dt.datetime.utcnow() - dt.timedelta(days=1)
    _, lic = await make_client_with_license(db_session, expires_at=past)
    resp = await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-001"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "LICENSE_EXPIRED"


@pytest.mark.asyncio
async def test_activate_version_mismatch(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    resp = await api_client.post("/api/activate", json={
        "key": lic.key, "device_id": "dev-001",
        "key_version": 999,  # неверная версия
    })
    assert resp.status_code == 409
    assert resp.json()["code"] == "VERSION_MISMATCH"


# ── deactivate ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deactivate_success(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-001"})
    resp = await api_client.post("/api/deactivate", json={"key": lic.key, "device_id": "dev-001"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["code"] == "DEACTIVATED"


@pytest.mark.asyncio
async def test_deactivate_not_activated(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    resp = await api_client.post("/api/deactivate", json={"key": lic.key, "device_id": "dev-001"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "NOT_ACTIVATED"


@pytest.mark.asyncio
async def test_deactivate_device_mismatch(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-001"})
    resp = await api_client.post("/api/deactivate", json={"key": lic.key, "device_id": "dev-999"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "DEVICE_MISMATCH"


# ── transfer ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transfer_success(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    old_key = lic.key
    await api_client.post("/api/activate", json={"key": old_key, "device_id": "dev-001"})

    resp = await api_client.post("/api/transfer", json={"key": old_key, "device_id": "dev-001"})
    assert resp.status_code == 200
    data = resp.json()
    assert "new_key" in data
    new_key = data["new_key"]
    assert new_key != old_key
    # Новый ключ имеет нужный формат XXXX-XXXX-XXXX
    parts = new_key.split("-")
    assert len(parts) == 3
    assert all(len(p) == 4 for p in parts)


@pytest.mark.asyncio
async def test_transfer_not_activated(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    resp = await api_client.post("/api/transfer", json={"key": lic.key, "device_id": "dev-001"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "NOT_ACTIVATED"


@pytest.mark.asyncio
async def test_transfer_after_deactivate(api_client, db_session):
    """После деактивации (released) transfer на другой ключ даёт новый."""
    _, lic = await make_client_with_license(db_session)
    old_key = lic.key
    await api_client.post("/api/activate", json={"key": old_key, "device_id": "dev-001"})

    # Переносим → новый ключ
    r1 = await api_client.post("/api/transfer", json={"key": old_key, "device_id": "dev-001"})
    new_key = r1.json()["new_key"]

    # Активируем на новом устройстве с новым ключом
    r2 = await api_client.post("/api/activate", json={"key": new_key, "device_id": "dev-002"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "activated"


# ── status & history ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_not_activated(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    resp = await api_client.get("/api/status", params={"key": lic.key})
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_activated"


@pytest.mark.asyncio
async def test_status_after_activation(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-001"})
    resp = await api_client.get("/api/status", params={"key": lic.key})
    assert resp.status_code == 200
    assert resp.json()["status"] == "activated"


@pytest.mark.asyncio
async def test_history_returns_actions(api_client, db_session):
    _, lic = await make_client_with_license(db_session)
    await api_client.post("/api/activate", json={"key": lic.key, "device_id": "dev-001"})
    resp = await api_client.get("/api/history", params={"key": lic.key})
    assert resp.status_code == 200
    data = resp.json()
    assert "keys" in data
    assert "actions" in data
    assert any(a["action"] == "activate" for a in data["actions"])


@pytest.mark.asyncio
async def test_history_not_found(api_client, db_session):
    resp = await api_client.get("/api/history", params={"key": "XXXX-YYYY-ZZZZ"})
    assert resp.status_code == 404
