"""
Публичная форма обратной связи.

GET  /feedback — страница с формой (без авторизации)
POST /feedback — сохранить обращение, отправить уведомление на admin email
"""
import datetime as dt
import hashlib
import hmac
import random

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import app_config, smtp_config
from app.db import get_session
from app.email import notify_feedback_received
from app.models import Client, Feedback, LoginAttempt
from app.security import get_current_org, get_current_owner

router = APIRouter(tags=["feedback"])
templates = Jinja2Templates(directory="templates")

SUBJECTS = [
    "Проблема с ключом",
    "Вопрос по активации",
    "Техническая проблема",
    "Запрос на увеличение лимита",
    "Другое",
]

_RATE_LIMIT = 3       # обращений
_RATE_WINDOW = 3600   # секунд (1 час)


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── captcha helpers ────────────────────────────────────────────────────────────

def _captcha_sign(answer: int) -> str:
    """Подписывает правильный ответ капчи HMAC-SHA256."""
    key = app_config.secret_key.encode()
    return hmac.new(key, str(answer).encode(), hashlib.sha256).hexdigest()


def _captcha_verify(user_answer: str, sig: str) -> bool:
    """Проверяет ответ пользователя по подписи."""
    try:
        answer = int(user_answer.strip())
    except (ValueError, AttributeError):
        return False
    expected_sig = _captcha_sign(answer)
    return hmac.compare_digest(expected_sig, sig)


def _new_captcha() -> tuple[str, str]:
    """Возвращает (вопрос, подпись ответа)."""
    a = random.randint(1, 9)
    b = random.randint(1, 9)
    question = f"Сколько будет {a} + {b}?"
    sig = _captcha_sign(a + b)
    return question, sig


# ── rate limit helper ─────────────────────────────────────────────────────────

async def _check_rate_limit(ip: str, db: AsyncSession) -> bool:
    """Возвращает True если лимит не превышен."""
    window_start = dt.datetime.utcnow() - dt.timedelta(seconds=_RATE_WINDOW)
    count = (await db.execute(
        select(func.count()).where(
            LoginAttempt.ip_address == ip,
            LoginAttempt.login == "feedback",
            LoginAttempt.at >= window_start,
        )
    )).scalar() or 0
    return count < _RATE_LIMIT


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/feedback", response_class=HTMLResponse)
async def feedback_get(
    request: Request,
    db: AsyncSession = Depends(get_session),
):
    # Определяем, кто зашёл: org, owner или анонимный
    org = await get_current_org(request, db)
    owner = None if org else await get_current_owner(request, db)

    # Предзаполнение полей
    prefill_org = org.org_name if org else ""
    prefill_email = (org.contact_email if org else (owner.email if owner else "")) or ""
    is_authenticated = bool(org or owner)

    captcha_q, captcha_sig = _new_captcha()

    return templates.TemplateResponse("feedback.html", {
        "request": request,
        "subjects": SUBJECTS,
        "prefill_org": prefill_org,
        "prefill_email": prefill_email,
        "is_authenticated": is_authenticated,
        "captcha_q": captcha_q,
        "captcha_sig": captcha_sig,
        "errors": [],
        "success": False,
    })


@router.post("/feedback", response_class=HTMLResponse)
async def feedback_post(
    request: Request,
    org_name: str = Form(...),
    contact_email: str = Form(""),
    subject: str = Form(...),
    message: str = Form(...),
    captcha_answer: str = Form(""),
    captcha_sig: str = Form(""),
    db: AsyncSession = Depends(get_session),
):
    ip = _get_ip(request)
    org = await get_current_org(request, db)
    owner = None if org else await get_current_owner(request, db)
    is_authenticated = bool(org or owner)

    errors: list[str] = []

    # Rate limit
    if not await _check_rate_limit(ip, db):
        errors.append("Слишком много обращений. Попробуйте через час.")

    # Валидация обязательных полей
    if not org_name.strip():
        errors.append("Укажите название организации.")
    if not subject or subject not in SUBJECTS:
        errors.append("Выберите тему из списка.")
    if not message.strip():
        errors.append("Введите текст обращения.")
    if len(message) > 2000:
        errors.append("Сообщение не должно превышать 2000 символов.")

    # Капча для анонимных пользователей
    if not is_authenticated:
        if not _captcha_verify(captcha_answer, captcha_sig):
            errors.append("Неверный ответ на проверочный вопрос.")

    if errors:
        captcha_q, new_captcha_sig = _new_captcha()
        prefill_email = contact_email
        return templates.TemplateResponse("feedback.html", {
            "request": request,
            "subjects": SUBJECTS,
            "prefill_org": org_name,
            "prefill_email": prefill_email,
            "is_authenticated": is_authenticated,
            "captcha_q": captcha_q,
            "captcha_sig": new_captcha_sig,
            "errors": errors,
            "success": False,
            "form_subject": subject,
            "form_message": message,
        }, status_code=422)

    # Определяем entity_type и entity_id
    if org:
        entity_type = "org"
        entity_id = org.id
        stored_org_name = org.org_name
        stored_email = org.contact_email or contact_email.strip() or None
    elif owner:
        entity_type = "admin"
        entity_id = owner.id
        stored_org_name = org_name.strip()
        stored_email = owner.email
    else:
        entity_type = "anonymous"
        entity_id = None
        stored_org_name = org_name.strip()
        stored_email = contact_email.strip() or None

    fb = Feedback(
        entity_type=entity_type,
        entity_id=entity_id,
        org_name=stored_org_name,
        contact_email=stored_email,
        subject=subject,
        message=message.strip(),
        ip_address=ip,
        user_agent=request.headers.get("User-Agent"),
    )
    db.add(fb)

    # Записать rate-limit attempt
    db.add(LoginAttempt(ip_address=ip, login="feedback", success=True))
    await db.commit()

    # Уведомление администратору
    if smtp_config.from_addr:
        admin_url = str(request.base_url).rstrip("/") + "/owner/feedback"
        notify_feedback_received(
            to=smtp_config.from_addr,
            org_name=stored_org_name,
            contact_email=stored_email or "",
            subject=subject,
            message=message.strip(),
            admin_url=admin_url,
        )

    return templates.TemplateResponse("feedback.html", {
        "request": request,
        "subjects": SUBJECTS,
        "prefill_org": "",
        "prefill_email": "",
        "is_authenticated": is_authenticated,
        "captcha_q": "",
        "captcha_sig": "",
        "errors": [],
        "success": True,
    })
