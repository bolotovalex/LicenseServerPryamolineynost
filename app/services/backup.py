"""
Резервное копирование и восстановление БД.

Формат: JSON с base64 для бинарных полей и ISO-8601 для datetime.
Порядок таблиц соответствует зависимостям FK.
"""
import base64
import datetime as dt
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import AdminUser, AppSetting, Client, License, LicenseAction, LicenseKey

# Порядок важен: сначала родители, потом дети (для INSERT).
# При удалении используем обратный порядок.
_MODELS = [AdminUser, AppSetting, Client, License, LicenseKey, LicenseAction]


def _serialize_row(obj) -> dict:
    d = {}
    for col in obj.__table__.columns:
        val = getattr(obj, col.name)
        if isinstance(val, bytes):
            d[col.name] = {"_b64": base64.b64encode(val).decode()}
        elif isinstance(val, dt.datetime):
            d[col.name] = {"_dt": val.isoformat()}
        else:
            d[col.name] = val
    return d


def _deserialize_row(d: dict) -> dict:
    result = {}
    for k, v in d.items():
        if isinstance(v, dict) and "_b64" in v:
            v = base64.b64decode(v["_b64"])
        elif isinstance(v, dict) and "_dt" in v:
            v = dt.datetime.fromisoformat(v["_dt"])
        result[k] = v
    return result


async def create_backup(session: AsyncSession) -> bytes:
    """Сериализует все таблицы в JSON. Бинарные поля — base64."""
    dump: dict = {
        "version": 1,
        "created_at": dt.datetime.utcnow().isoformat() + "Z",
        "tables": {},
    }
    for model in _MODELS:
        rows = (await session.execute(select(model))).scalars().all()
        dump["tables"][model.__tablename__] = [_serialize_row(r) for r in rows]

    return json.dumps(dump, ensure_ascii=False, indent=2).encode("utf-8")


async def restore_backup(engine: AsyncEngine, data: bytes) -> dict[str, int]:
    """
    Восстанавливает БД из JSON-дампа.
    Все текущие данные УДАЛЯЮТСЯ перед вставкой.
    Возвращает словарь {table_name: кол-во_строк}.
    """
    dump = json.loads(data.decode("utf-8"))
    stats: dict[str, int] = {}

    async with engine.begin() as conn:
        # Удаляем в обратном порядке (дети раньше родителей)
        for model in reversed(_MODELS):
            await conn.execute(model.__table__.delete())

        for model in _MODELS:
            rows = dump["tables"].get(model.__tablename__, [])
            for row_dict in rows:
                await conn.execute(
                    model.__table__.insert().values(**_deserialize_row(row_dict))
                )
            stats[model.__tablename__] = len(rows)

    return stats
