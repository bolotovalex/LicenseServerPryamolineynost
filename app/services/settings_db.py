"""
Настройки приложения, хранящиеся в таблице app_settings.

Жизненный цикл:
  1. Первый запуск: sync_from_config() переносит значения из config/*.cfg в БД.
  2. Последующие запуски: существующие ключи не перезаписываются.
  3. При восстановлении резервной копии настройки восстанавливаются вместе с остальными данными.
"""
import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting

# Описания ключей (отображаются в UI)
SETTING_DESCRIPTIONS: dict[str, str] = {
    "app.name": "Название приложения",
    "app.jwt_algorithm": "Алгоритм JWT (HS256 / HS384 / HS512)",
    "app.token_expires_hours": "Время жизни токена администратора (часы)",
    "app.debug": "Режим отладки (true/false)",
    "smtp.host": "SMTP-сервер",
    "smtp.port": "SMTP-порт",
    "smtp.user": "SMTP-пользователь",
    "smtp.from": "Адрес отправителя",
    "smtp.tls": "Использовать STARTTLS (true/false)",
    "smtp.enabled": "Включить отправку почты (true/false)",
    "security.brute_force.max_attempts": "Максимум неудачных попыток входа",
    "security.brute_force.lockout_minutes": "Блокировка после превышения (минуты)",
    "security.password.min_length": "Минимальная длина пароля",
}


async def sync_from_config(session: AsyncSession) -> None:
    """
    Копирует значения из config/*.cfg в app_settings, если ключ ещё не существует.
    Вызывать при старте приложения.
    """
    from app.config import app_config, security_config, smtp_config

    defaults = {
        "app.name": app_config.name,
        "app.jwt_algorithm": app_config.jwt_algorithm,
        "app.token_expires_hours": str(app_config.token_expires_minutes // 60),
        "app.debug": str(app_config.debug).lower(),
        "smtp.host": smtp_config.host,
        "smtp.port": str(smtp_config.port),
        "smtp.user": smtp_config.user,
        "smtp.from": smtp_config.from_addr,
        "smtp.tls": str(smtp_config.tls).lower(),
        "smtp.enabled": str(smtp_config.enabled).lower(),
        "security.brute_force.max_attempts": str(security_config.max_attempts),
        "security.brute_force.lockout_minutes": str(security_config.lockout_minutes),
        "security.password.min_length": str(security_config.password_min_length),
    }

    existing_keys = set(
        (await session.execute(select(AppSetting.key))).scalars().all()
    )

    for key, value in defaults.items():
        if key not in existing_keys:
            session.add(AppSetting(
                key=key,
                value=value,
                description=SETTING_DESCRIPTIONS.get(key),
                updated_at=dt.datetime.utcnow(),
            ))

    await session.commit()


async def get_setting(session: AsyncSession, key: str, default: str | None = None) -> str | None:
    row = (await session.execute(
        select(AppSetting).where(AppSetting.key == key)
    )).scalar_one_or_none()
    return row.value if row else default


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = (await session.execute(
        select(AppSetting).where(AppSetting.key == key)
    )).scalar_one_or_none()
    if row:
        row.value = value
        row.updated_at = dt.datetime.utcnow()
    else:
        session.add(AppSetting(
            key=key,
            value=value,
            description=SETTING_DESCRIPTIONS.get(key),
            updated_at=dt.datetime.utcnow(),
        ))
    await session.commit()


async def get_all_settings(session: AsyncSession) -> list[AppSetting]:
    return (await session.execute(
        select(AppSetting).order_by(AppSetting.key)
    )).scalars().all()
