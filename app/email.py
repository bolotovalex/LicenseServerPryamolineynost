"""
Отправка email-уведомлений через aiosmtplib.
Настройки берутся из smtp_config (config/smtp.cfg).

Публичные notify_* — fire-and-forget через asyncio.create_task().
"""
import asyncio
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.config import smtp_config

logger = logging.getLogger(__name__)

_jinja = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent.parent / "templates" / "email")),
    autoescape=True,
)


# ── core ──────────────────────────────────────────────────────────────────────

async def send_email(to: str, subject: str, body_html: str) -> bool:
    """Отправляет письмо. Возвращает True при успехе."""
    if not smtp_config.enabled:
        logger.info("EMAIL DISABLED: to=%s | subject=%s", to, subject)
        return True

    if not to:
        return False

    try:
        import aiosmtplib

        msg = MIMEMultipart("alternative")
        msg["From"] = smtp_config.from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body_html, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=smtp_config.host,
            port=smtp_config.port,
            username=smtp_config.user or None,
            password=smtp_config.password or None,
            start_tls=smtp_config.tls,
        )
        logger.info("Email sent: to=%s | subject=%s", to, subject)
        return True

    except Exception as exc:
        logger.error("SMTP error: %s | to=%s | subject=%s", exc, to, subject)
        return False


def _render(template_name: str, **ctx) -> str:
    return _jinja.get_template(template_name).render(**ctx)


# ── private senders ───────────────────────────────────────────────────────────

async def _send_org_created(client, plain_password: str) -> None:
    if not client.contact_email:
        return
    await send_email(
        to=client.contact_email,
        subject=f"Доступ к личному кабинету — {client.org_name}",
        body_html=_render("org_created.html", client=client, password=plain_password),
    )


async def _send_key_issued(client, license) -> None:
    if not client.contact_email:
        return
    await send_email(
        to=client.contact_email,
        subject="Выпущен новый лицензионный ключ",
        body_html=_render("key_issued.html", client=client, license=license),
    )


async def _send_key_reset(client, license, reason: str) -> None:
    if not client.contact_email:
        return
    await send_email(
        to=client.contact_email,
        subject="Лицензионный ключ сброшен",
        body_html=_render("key_reset.html", client=client, license=license, reason=reason),
    )


async def _send_key_blocked(client, license, reason: str) -> None:
    if not client.contact_email:
        return
    await send_email(
        to=client.contact_email,
        subject="Лицензионный ключ заблокирован",
        body_html=_render("key_blocked.html", client=client, license=license, reason=reason),
    )


async def _send_password_reset(email: str, reset_url: str) -> None:
    await send_email(
        to=email,
        subject="Сброс пароля — License Server",
        body_html=_render("password_reset.html", reset_url=reset_url),
    )


# ── public API (fire-and-forget) ──────────────────────────────────────────────

def notify_org_created(client, plain_password: str) -> None:
    """Уведомление о создании организации с учётными данными для входа."""
    asyncio.create_task(_send_org_created(client, plain_password))


def notify_key_issued(client, license) -> None:
    """Уведомление о выпуске нового лицензионного ключа."""
    asyncio.create_task(_send_key_issued(client, license))


def notify_key_reset(client, license, reason: str) -> None:
    """Уведомление о сбросе лицензионного ключа."""
    asyncio.create_task(_send_key_reset(client, license, reason))


def notify_key_blocked(client, license, reason: str) -> None:
    """Уведомление о блокировке лицензионного ключа."""
    asyncio.create_task(_send_key_blocked(client, license, reason))


def notify_password_reset(email: str, reset_url: str) -> None:
    """Уведомление со ссылкой на сброс пароля."""
    asyncio.create_task(_send_password_reset(email, reset_url))
