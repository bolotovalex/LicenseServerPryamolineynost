"""
Тесты аутентификации.
- Вход org по login
- Вход org по contact_email
- Неверный пароль
- Неактивный клиент
"""
import pytest

from app.models import Client
from app.security import hash_password


async def _make_org(db, *, login="orglogin", contact_email="org@example.com", is_active=True):
    org = Client(
        org_name="Auth Test Org",
        login=login,
        password_hash=hash_password("Secret1!"),
        contact_email=contact_email,
        is_active=is_active,
        max_keys=3,
    )
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org


@pytest.mark.asyncio
async def test_login_by_login(api_client, db_session):
    await _make_org(db_session)
    resp = await api_client.post(
        "/login",
        data={"login": "orglogin", "password": "Secret1!"},
        follow_redirects=False,
    )
    # Успешный вход → редирект на /org/dashboard
    assert resp.status_code in (302, 303)
    assert "org" in resp.headers.get("location", "").lower()


@pytest.mark.asyncio
async def test_login_by_email(api_client, db_session):
    await _make_org(db_session, contact_email="unique@example.com")
    resp = await api_client.post(
        "/login",
        data={"login": "unique@example.com", "password": "Secret1!"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    assert "org" in resp.headers.get("location", "").lower()


@pytest.mark.asyncio
async def test_login_wrong_password(api_client, db_session):
    await _make_org(db_session)
    resp = await api_client.post(
        "/login",
        data={"login": "orglogin", "password": "wrongpass"},
        follow_redirects=False,
    )
    # Остаёмся на странице логина (200 с формой или редирект обратно на /login)
    assert resp.status_code in (200, 302, 303)
    location = resp.headers.get("location", "")
    if resp.status_code in (302, 303):
        assert "dashboard" not in location


@pytest.mark.asyncio
async def test_login_inactive_client(api_client, db_session):
    await _make_org(db_session, is_active=False)
    resp = await api_client.post(
        "/login",
        data={"login": "orglogin", "password": "Secret1!"},
        follow_redirects=False,
    )
    # Не должен перейти на dashboard
    location = resp.headers.get("location", "")
    assert "dashboard" not in location


@pytest.mark.asyncio
async def test_login_unknown_user(api_client, db_session):
    resp = await api_client.post(
        "/login",
        data={"login": "nobody", "password": "pass"},
        follow_redirects=False,
    )
    location = resp.headers.get("location", "")
    assert "dashboard" not in location
