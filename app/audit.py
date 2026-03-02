"""
Утилита аудита: сохраняет запись в AuditLog и пишет строку в logs/audit.log.
"""
import json
import logging

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

_audit_log = logging.getLogger("audit")


def _get_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _get_ua(request: Request | None) -> str | None:
    if request is None:
        return None
    return request.headers.get("User-Agent")


async def log_action(
    db: AsyncSession,
    actor_type: str,
    action: str,
    actor_id: int | None = None,
    actor_login: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    details: dict | None = None,
    success: bool = True,
    request: Request | None = None,
) -> None:
    """Сохраняет событие в таблицу AuditLog и в logs/audit.log."""
    ip = _get_ip(request)
    ua = _get_ua(request)
    details_str = json.dumps(details, ensure_ascii=False) if details else None

    entry = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        actor_login=actor_login,
        ip_address=ip,
        user_agent=ua,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details_str,
        success=success,
    )
    db.add(entry)
    await db.flush()  # получить entry.id без commit

    # строка в audit.log
    parts = [
        f"actor={actor_type}:{actor_login or actor_id or '-'}",
        f"action={action}",
        f"success={success}",
    ]
    if entity_type:
        parts.append(f"entity={entity_type}:{entity_id or '-'}")
    if ip:
        parts.append(f"ip={ip}")
    if details_str:
        parts.append(f"details={details_str}")
    _audit_log.info(" | ".join(parts))
