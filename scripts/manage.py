#!/usr/bin/env python
"""
scripts/manage.py — интерактивная консоль управления License Server.

Запуск: python scripts/manage.py
"""
import asyncio
import csv
import datetime as dt
import getpass
import os
import sys
from pathlib import Path

# Корень проекта в sys.path для импорта app.*
sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from sqlalchemy import delete, func, select, text

from app.audit import log_action
from app.config import db_config
from app.db import AsyncSessionLocal
from app.models import (
    AdminUser, AuditLog, Client, License, LicenseAction, LoginAttempt,
)
from app.password import generate_password, validate_password
from app.security import hash_password


# ── ANSI цвета ────────────────────────────────────────────────────────────────

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


# ── Вывод ─────────────────────────────────────────────────────────────────────

def ok(msg):   print(f"{GREEN}✓ {msg}{RESET}")
def err(msg):  print(f"{RED}✗ {msg}{RESET}")
def warn(msg): print(f"{YELLOW}⚠ {msg}{RESET}")
def info(msg): print(f"{CYAN}  {msg}{RESET}")
def hdr(msg):  print(f"\n{BOLD}{msg}{RESET}\n")


def fmt_dt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def is_locked(entity) -> bool:
    return bool(
        entity.locked_until and entity.locked_until > dt.datetime.utcnow()
    )


# ── Ввод ──────────────────────────────────────────────────────────────────────

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{YELLOW}{prompt}{suffix}: {RESET}").strip()
    return val if val else default


def ask_int(prompt: str, default: int | None = None,
            min_val: int | None = None, max_val: int | None = None) -> int:
    while True:
        raw = ask(prompt, str(default) if default is not None else "")
        if not raw and default is not None:
            return default
        try:
            val = int(raw)
        except ValueError:
            err("Введите целое число")
            continue
        if min_val is not None and val < min_val:
            err(f"Минимальное значение: {min_val}")
            continue
        if max_val is not None and val > max_val:
            err(f"Максимальное значение: {max_val}")
            continue
        return val


def confirm(prompt: str) -> bool:
    return ask(prompt + " [y/N]").lower() == "y"


def pause():
    try:
        input(f"\n{YELLOW}  Нажмите Enter для продолжения...{RESET}")
    except KeyboardInterrupt:
        raise


# ── Таблица ───────────────────────────────────────────────────────────────────

def print_table(headers: list[str], rows: list[list], max_col: int = 38) -> None:
    if not rows:
        info("(нет данных)")
        return

    # Ширины колонок
    widths = [len(h) for h in headers]
    str_rows: list[list[str]] = []
    for row in rows:
        sr = []
        for cell in row:
            sr.append("—" if cell is None else str(cell))
        str_rows.append(sr)
        for i, cell in enumerate(sr):
            widths[i] = min(max(widths[i], len(cell)), max_col)

    def _cell(s: str, w: int) -> str:
        s = str(s)
        if len(s) > w:
            s = s[:w - 1] + "…"
        return f"{s:<{w}}"

    sep_inner = "─" * (sum(widths) + 3 * (len(headers) - 1))
    sep_top   = f"┌─{sep_inner}─┐"
    sep_mid   = f"├─{sep_inner}─┤"
    sep_bot   = f"└─{sep_inner}─┘"

    print(sep_top)
    h_cells = " │ ".join(_cell(h, w) for h, w in zip(headers, widths))
    print(f"│ {BOLD}{h_cells}{RESET} │")
    print(sep_mid)
    for i, sr in enumerate(str_rows):
        row_s = " │ ".join(_cell(c, w) for c, w in zip(sr, widths))
        color = CYAN if i % 2 == 0 else ""
        print(f"│ {color}{row_s}{RESET} │")
    print(sep_bot)
    print(f"  {GREEN}Итого: {len(rows)}{RESET}")


# ── Меню ──────────────────────────────────────────────────────────────────────

def box_menu(title: str, items: list[str]) -> None:
    width = max(len(title), max(len(s) for s in items)) + 4
    border = "═" * width
    pad_l = (width - len(title)) // 2
    pad_r = width - pad_l - len(title)
    print(f"\n{BOLD}╔{border}╗")
    print(f"║{' ' * pad_l}{title}{' ' * pad_r}║")
    print(f"╠{border}╣")
    for item in items:
        print(f"║  {item:<{width - 2}}║")
    print(f"╚{border}╝{RESET}")


# ── asyncio.run() обёртка ─────────────────────────────────────────────────────

def db_run(coro):
    return asyncio.run(coro)


# ── Утилиты ввода пароля ──────────────────────────────────────────────────────

def _ask_new_password(allow_generate: bool = True) -> str | None:
    """
    Спрашивает новый пароль (ручной или сгенерированный).
    Возвращает plain-text пароль или None при отмене.
    """
    if allow_generate:
        print(f"  {CYAN}1{RESET}. Ввести вручную")
        print(f"  {CYAN}2{RESET}. Сгенерировать автоматически")
        mode = ask("Выберите", "1")
    else:
        mode = "1"

    if mode == "2":
        pw = generate_password()
        info(f"Сгенерирован пароль: {BOLD}{pw}{RESET}")
        return pw

    # Ручной ввод
    while True:
        try:
            pw = getpass.getpass(f"{YELLOW}  Новый пароль: {RESET}")
            pw2 = getpass.getpass(f"{YELLOW}  Подтверждение: {RESET}")
        except KeyboardInterrupt:
            print()
            warn("Ввод пароля отменён")
            return None
        if pw != pw2:
            err("Пароли не совпадают")
            continue
        errs = validate_password(pw)
        if errs:
            for e in errs:
                err(e)
            continue
        return pw


# ═══════════════════════════════════════════════════════════════════════════════
# ── Подменю 1: Администраторы ──────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

async def _admin_list() -> list:
    async with AsyncSessionLocal() as db:
        admins = (await db.execute(
            select(AdminUser).order_by(AdminUser.id)
        )).scalars().all()
        rows = []
        for u in admins:
            rows.append([
                u.id,
                u.email,
                u.role,
                "✓" if u.is_active else "✗",
                fmt_dt(u.last_login_at),
                fmt_dt(u.locked_until) if is_locked(u) else "—",
            ])
        return rows


async def _admin_find(email: str) -> AdminUser | None:
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(AdminUser).where(AdminUser.email == email)
        )).scalar_one_or_none()


async def _admin_reset_password(email: str, new_hash: str) -> tuple[bool, str]:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(
            select(AdminUser).where(AdminUser.email == email)
        )).scalar_one_or_none()
        if not user:
            return False, "Администратор не найден"
        user.password_hash = new_hash
        user.failed_attempts = 0
        user.locked_until = None
        await log_action(db, "cli", "password_reset",
                         actor_login="cli",
                         entity_type="admin", entity_id=user.id,
                         details={"email": email})
        await db.commit()
        return True, ""


async def _admin_create(email: str, role: str, pw_hash: str) -> tuple[bool, str]:
    async with AsyncSessionLocal() as db:
        exists = (await db.execute(
            select(AdminUser).where(AdminUser.email == email)
        )).scalar_one_or_none()
        if exists:
            return False, "Email уже занят"
        user = AdminUser(email=email, password_hash=pw_hash,
                         role=role, is_active=True)
        db.add(user)
        await log_action(db, "cli", "create_admin",
                         actor_login="cli",
                         entity_type="admin",
                         details={"email": email, "role": role})
        await db.commit()
        await db.refresh(user)
        return True, str(user.id)


async def _admin_toggle_active(email: str) -> tuple[bool, bool | str]:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(
            select(AdminUser).where(AdminUser.email == email)
        )).scalar_one_or_none()
        if not user:
            return False, "Не найден"
        user.is_active = not user.is_active
        await log_action(db, "cli", "toggle_admin_active",
                         actor_login="cli",
                         entity_type="admin", entity_id=user.id,
                         details={"email": email, "is_active": user.is_active})
        await db.commit()
        return True, user.is_active


async def _admin_unlock(email: str) -> tuple[bool, str]:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(
            select(AdminUser).where(AdminUser.email == email)
        )).scalar_one_or_none()
        if not user:
            return False, "Не найден"
        user.failed_attempts = 0
        user.locked_until = None
        await log_action(db, "cli", "unlock_admin",
                         actor_login="cli",
                         entity_type="admin", entity_id=user.id,
                         details={"email": email})
        await db.commit()
        return True, ""


def _show_admin_info(user: AdminUser) -> None:
    info(f"Email:   {user.email}")
    info(f"Роль:    {user.role}")
    info(f"Активен: {'да' if user.is_active else 'нет'}")
    if is_locked(user):
        warn(f"Заблокирован до: {fmt_dt(user.locked_until)}")
    print()


def menu_admins() -> None:
    while True:
        box_menu("Администраторы", [
            "1. Список",
            "2. Сброс пароля",
            "3. Создать",
            "4. Активировать / деактивировать",
            "5. Снять блокировку",
            "0. Назад",
        ])
        choice = ask("Выберите")

        # ── 1. Список ────────────────────────────────────────────────────────
        if choice == "0":
            break

        elif choice == "1":
            hdr("Список администраторов")
            print_table(
                ["ID", "Email", "Роль", "Активен", "Последний вход", "Заблокирован до"],
                db_run(_admin_list()),
            )
            pause()

        # ── 2. Сброс пароля ──────────────────────────────────────────────────
        elif choice == "2":
            hdr("Сброс пароля администратора")
            email = ask("Email")
            if not email:
                continue
            user = db_run(_admin_find(email))
            if not user:
                err(f"Администратор '{email}' не найден")
                continue
            _show_admin_info(user)

            pw = _ask_new_password()
            if pw is None:
                continue
            if not confirm(f"Сбросить пароль для {email}?"):
                warn("Отменено")
                continue

            ok_, msg = db_run(_admin_reset_password(email, hash_password(pw)))
            if ok_:
                ok(f"Пароль изменён.")
                info(f"Новый пароль: {BOLD}{pw}{RESET}")
            else:
                err(msg)

        # ── 3. Создать ───────────────────────────────────────────────────────
        elif choice == "3":
            hdr("Создание администратора")
            email = ask("Email")
            if not email:
                continue
            print(f"  Роли: {CYAN}admin{RESET} | {CYAN}superadmin{RESET}")
            role = ask("Роль", "admin")
            if role not in ("admin", "superadmin"):
                err("Недопустимая роль. Используйте: admin, superadmin")
                continue

            pw = _ask_new_password()
            if pw is None:
                continue

            ok_, result = db_run(_admin_create(email, role, hash_password(pw)))
            if ok_:
                ok(f"Администратор создан (ID={result})")
                info(f"Email:  {email}")
                info(f"Пароль: {BOLD}{pw}{RESET}")
            else:
                err(result)

        # ── 4. Активировать / деактивировать ─────────────────────────────────
        elif choice == "4":
            hdr("Активация / деактивация администратора")
            email = ask("Email")
            if not email:
                continue
            ok_, result = db_run(_admin_toggle_active(email))
            if ok_:
                state = "активирован" if result else "деактивирован"
                ok(f"Администратор {email} {state}")
            else:
                err(str(result))

        # ── 5. Снять блокировку ───────────────────────────────────────────────
        elif choice == "5":
            hdr("Снятие блокировки администратора")
            email = ask("Email")
            if not email:
                continue
            ok_, msg = db_run(_admin_unlock(email))
            if ok_:
                ok(f"Блокировка снята для {email}")
            else:
                err(msg)

        else:
            warn("Неверный выбор")


# ═══════════════════════════════════════════════════════════════════════════════
# ── Подменю 2: Организации ─────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

async def _org_list() -> list:
    async with AsyncSessionLocal() as db:
        clients = (await db.execute(
            select(Client).order_by(Client.id)
        )).scalars().all()
        rows = []
        for c in clients:
            rows.append([
                c.id,
                c.login or "—",
                c.org_name,
                c.contact_email or "—",
                "✓" if c.is_active else "✗",
                fmt_dt(c.locked_until) if is_locked(c) else "—",
            ])
        return rows


async def _org_find(login: str) -> Client | None:
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(Client).where(Client.login == login)
        )).scalar_one_or_none()


async def _org_reset_password(login: str, new_hash: str) -> tuple[bool, str]:
    async with AsyncSessionLocal() as db:
        client = (await db.execute(
            select(Client).where(Client.login == login)
        )).scalar_one_or_none()
        if not client:
            return False, "Организация не найдена"
        client.password_hash = new_hash
        client.failed_attempts = 0
        client.locked_until = None
        await log_action(db, "cli", "password_reset",
                         actor_login="cli",
                         entity_type="org", entity_id=client.id,
                         details={"login": login})
        await db.commit()
        return True, ""


async def _org_toggle_active(login: str) -> tuple[bool, bool | str]:
    async with AsyncSessionLocal() as db:
        client = (await db.execute(
            select(Client).where(Client.login == login)
        )).scalar_one_or_none()
        if not client:
            return False, "Не найдена"
        client.is_active = not client.is_active
        await log_action(db, "cli", "toggle_org_active",
                         actor_login="cli",
                         entity_type="org", entity_id=client.id,
                         details={"login": login, "is_active": client.is_active})
        await db.commit()
        return True, client.is_active


async def _org_unlock(login: str) -> tuple[bool, str]:
    async with AsyncSessionLocal() as db:
        client = (await db.execute(
            select(Client).where(Client.login == login)
        )).scalar_one_or_none()
        if not client:
            return False, "Не найдена"
        client.failed_attempts = 0
        client.locked_until = None
        await log_action(db, "cli", "unlock_org",
                         actor_login="cli",
                         entity_type="org", entity_id=client.id,
                         details={"login": login})
        await db.commit()
        return True, ""


async def _send_org_password_email(to: str, org_name: str, new_pw: str) -> None:
    from app.email import send_email
    body = (
        f"<p>Здравствуйте!</p>"
        f"<p>Администратор сбросил пароль для организации "
        f"<strong>{org_name}</strong>.</p>"
        f"<p>Новый пароль: <code style='font-size:15px'>{new_pw}</code></p>"
        f"<p>Рекомендуем сменить пароль сразу после входа.</p>"
    )
    await send_email(to=to, subject="Сброс пароля — License Server", body_html=body)


def _show_org_info(client: Client) -> None:
    info(f"Название: {client.org_name}")
    info(f"Логин:    {client.login or '—'}")
    info(f"Email:    {client.contact_email or '—'}")
    info(f"Активна:  {'да' if client.is_active else 'нет'}")
    if is_locked(client):
        warn(f"Заблокирована до: {fmt_dt(client.locked_until)}")
    print()


def menu_orgs() -> None:
    while True:
        box_menu("Организации", [
            "1. Список",
            "2. Сброс пароля",
            "3. Активировать / деактивировать",
            "4. Снять блокировку",
            "0. Назад",
        ])
        choice = ask("Выберите")

        if choice == "0":
            break

        # ── 1. Список ────────────────────────────────────────────────────────
        elif choice == "1":
            hdr("Список организаций")
            print_table(
                ["ID", "Логин", "Название", "Email", "Активна", "Заблокирована до"],
                db_run(_org_list()),
            )
            pause()

        # ── 2. Сброс пароля ──────────────────────────────────────────────────
        elif choice == "2":
            hdr("Сброс пароля организации")
            login = ask("Логин")
            if not login:
                continue
            client = db_run(_org_find(login))
            if not client:
                err(f"Организация с логином '{login}' не найдена")
                continue
            _show_org_info(client)

            pw = _ask_new_password()
            if pw is None:
                continue
            if not confirm(f"Сбросить пароль для '{login}'?"):
                warn("Отменено")
                continue

            ok_, msg = db_run(_org_reset_password(login, hash_password(pw)))
            if ok_:
                ok("Пароль изменён.")
                info(f"Новый пароль: {BOLD}{pw}{RESET}")

                if client.contact_email and confirm(f"Отправить уведомление на {client.contact_email}?"):
                    try:
                        db_run(_send_org_password_email(
                            client.contact_email, client.org_name, pw
                        ))
                        ok("Уведомление отправлено")
                    except Exception as exc:
                        err(f"Ошибка отправки: {exc}")
            else:
                err(msg)

        # ── 3. Активировать / деактивировать ─────────────────────────────────
        elif choice == "3":
            hdr("Активация / деактивация организации")
            login = ask("Логин")
            if not login:
                continue
            ok_, result = db_run(_org_toggle_active(login))
            if ok_:
                state = "активирована" if result else "деактивирована"
                ok(f"Организация '{login}' {state}")
            else:
                err(str(result))

        # ── 4. Снять блокировку ───────────────────────────────────────────────
        elif choice == "4":
            hdr("Снятие блокировки организации")
            login = ask("Логин")
            if not login:
                continue
            ok_, msg = db_run(_org_unlock(login))
            if ok_:
                ok(f"Блокировка снята для '{login}'")
            else:
                err(msg)

        else:
            warn("Неверный выбор")


# ═══════════════════════════════════════════════════════════════════════════════
# ── Подменю 3: Логи ────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

async def _log_logins(n: int) -> list:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(LoginAttempt).order_by(LoginAttempt.at.desc()).limit(n)
        )).scalars().all()
        return [
            [r.id, fmt_dt(r.at), r.ip_address, r.login, "✓" if r.success else "✗"]
            for r in rows
        ]


async def _log_failed(hours: int) -> list:
    async with AsyncSessionLocal() as db:
        since = dt.datetime.utcnow() - dt.timedelta(hours=hours)
        rows = (await db.execute(
            select(LoginAttempt)
            .where(LoginAttempt.success == False, LoginAttempt.at >= since)  # noqa: E712
            .order_by(LoginAttempt.at.desc())
        )).scalars().all()
        return [[r.id, fmt_dt(r.at), r.ip_address, r.login] for r in rows]


async def _log_license_actions(n: int) -> list:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(LicenseAction).order_by(LicenseAction.at.desc()).limit(n)
        )).scalars().all()
        return [
            [r.id, fmt_dt(r.at), r.license_id, r.action, r.reason or "—"]
            for r in rows
        ]


async def _log_export_csv(path: str, limit: int) -> int:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)
        )).scalars().all()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ID", "Время", "Тип актора", "ID актора", "Логин актора",
            "IP", "Действие", "Тип объекта", "ID объекта", "Детали", "Успех",
        ])
        for r in rows:
            writer.writerow([
                r.id, fmt_dt(r.at), r.actor_type, r.actor_id, r.actor_login,
                r.ip_address, r.action, r.entity_type, r.entity_id,
                r.details, r.success,
            ])
    return len(rows)


def menu_logs() -> None:
    while True:
        box_menu("Просмотр логов", [
            "1. Последние N входов",
            "2. Неудачные попытки за X часов",
            "3. Действия с лицензиями (последние N)",
            "4. Экспорт AuditLog в CSV",
            "0. Назад",
        ])
        choice = ask("Выберите")

        if choice == "0":
            break

        elif choice == "1":
            n = ask_int("Количество записей", default=20, min_val=1, max_val=1000)
            hdr(f"Последние {n} входов")
            print_table(
                ["ID", "Время", "IP", "Логин", "Успех"],
                db_run(_log_logins(n)),
            )
            pause()

        elif choice == "2":
            hours = ask_int("За последние X часов", default=24, min_val=1, max_val=720)
            hdr(f"Неудачные попытки за {hours} ч.")
            print_table(
                ["ID", "Время", "IP", "Логин"],
                db_run(_log_failed(hours)),
            )
            pause()

        elif choice == "3":
            n = ask_int("Количество записей", default=20, min_val=1, max_val=1000)
            hdr(f"Последние {n} действий с лицензиями")
            print_table(
                ["ID", "Время", "Лицензия", "Действие", "Причина"],
                db_run(_log_license_actions(n)),
            )
            pause()

        elif choice == "4":
            path = ask("Путь к файлу", "audit_export.csv")
            limit = ask_int("Максимум записей", default=1000, min_val=1, max_val=100000)
            try:
                count = db_run(_log_export_csv(path, limit))
                ok(f"Экспортировано {count} записей → {path}")
            except Exception as exc:
                err(f"Ошибка: {exc}")

        else:
            warn("Неверный выбор")


# ═══════════════════════════════════════════════════════════════════════════════
# ── Подменю 4: Обслуживание БД ─────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

async def _db_stats() -> dict:
    async with AsyncSessionLocal() as db:
        return {
            "Администраторы":         (await db.execute(select(func.count()).select_from(AdminUser))).scalar(),
            "Организации":            (await db.execute(select(func.count()).select_from(Client))).scalar(),
            "Лицензии":               (await db.execute(select(func.count()).select_from(License))).scalar(),
            "Действия с лицензиями":  (await db.execute(select(func.count()).select_from(LicenseAction))).scalar(),
            "Попытки входа":          (await db.execute(select(func.count()).select_from(LoginAttempt))).scalar(),
            "Записи аудита":          (await db.execute(select(func.count()).select_from(AuditLog))).scalar(),
        }


async def _db_clean_logins(days: int) -> int:
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=days)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(LoginAttempt).where(LoginAttempt.at < cutoff)
        )
        await db.commit()
        return result.rowcount


async def _db_clean_audit(days: int) -> int:
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=days)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(AuditLog).where(AuditLog.at < cutoff)
        )
        await db.commit()
        return result.rowcount


async def _db_integrity() -> list[str]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text("PRAGMA integrity_check"))).fetchall()
        return [r[0] for r in rows]


def menu_db() -> None:
    while True:
        box_menu("Обслуживание БД", [
            "1. Статистика",
            "2. Очистить LoginAttempt старше N дней",
            "3. Очистить AuditLog старше N дней",
            "4. Проверить целостность (PRAGMA)",
            "0. Назад",
        ])
        choice = ask("Выберите")

        if choice == "0":
            break

        elif choice == "1":
            hdr("Статистика БД")
            stats = db_run(_db_stats())
            print_table(
                ["Объект", "Количество"],
                [[k, v] for k, v in stats.items()],
            )
            pause()

        elif choice == "2":
            days = ask_int("Удалить записи старше N дней", default=90, min_val=1)
            if confirm(f"Удалить LoginAttempt старше {days} дн.?"):
                count = db_run(_db_clean_logins(days))
                ok(f"Удалено {count} записей")
            else:
                warn("Отменено")

        elif choice == "3":
            days = ask_int("Удалить записи старше N дней", default=180, min_val=1)
            if confirm(f"Удалить AuditLog старше {days} дн.?"):
                count = db_run(_db_clean_audit(days))
                ok(f"Удалено {count} записей")
            else:
                warn("Отменено")

        elif choice == "4":
            hdr("Проверка целостности БД")
            results = db_run(_db_integrity())
            if results == ["ok"]:
                ok("integrity_check → ok  (БД в порядке)")
            else:
                err(f"Обнаружены проблемы ({len(results)}):")
                for r in results:
                    print(f"    {RED}{r}{RESET}")
            pause()

        else:
            warn("Неверный выбор")


# ═══════════════════════════════════════════════════════════════════════════════
# ── main ───────────────────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    try:
        while True:
            box_menu("License Server Manager", [
                "1. Управление администраторами",
                "2. Управление организациями",
                "3. Просмотр логов",
                "4. Обслуживание БД",
                "0. Выход",
            ])
            choice = ask("Выберите")

            if choice == "0":
                print(f"\n{YELLOW}До свидания!{RESET}\n")
                sys.exit(0)
            elif choice == "1":
                menu_admins()
            elif choice == "2":
                menu_orgs()
            elif choice == "3":
                menu_logs()
            elif choice == "4":
                menu_db()
            else:
                warn("Неверный выбор")

    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}До свидания!{RESET}\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
