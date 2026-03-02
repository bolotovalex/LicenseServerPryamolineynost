#!/usr/bin/env python
"""
License Server — сервисные команды CLI.

Использование:
  python cli.py --help
  python cli.py backup -o backup.json
  python cli.py restore backup.json
  python cli.py create-admin admin@example.com
  python cli.py list-clients
  python cli.py db-init
"""
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
import asyncio
import sys
from pathlib import Path

import click

# Корень проекта в sys.path, чтобы импортировать app.*
sys.path.insert(0, str(Path(__file__).parent))


def run(coro):
    return asyncio.run(coro)


async def _ensure_db():
    """Создаёт таблицы, если их нет (для CLI без uvicorn)."""
    from app.db import engine, Base
    import app.models  # noqa: F401 — зарегистрировать все модели
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# ======================================================================

@click.group()
def cli():
    """License Server — управление через командную строку."""


# ---------------------------------------------------------------- backup

@cli.command("backup")
@click.option("-o", "--output", default="backup.json", show_default=True,
              help="Путь к файлу резервной копии")
def cmd_backup(output):
    """Создать резервную копию БД -> JSON-файл."""
    async def _():
        await _ensure_db()
        from app.db import AsyncSessionLocal
        from app.services.backup import create_backup
        async with AsyncSessionLocal() as s:
            data = await create_backup(s)
        Path(output).write_bytes(data)
        click.echo(f"Резервная копия сохранена: {output!r}  ({len(data):,} байт)")
    run(_())


@cli.command("restore")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--yes", "-y", is_flag=True, help="Не спрашивать подтверждение")
def cmd_restore(file, yes):
    """Восстановить БД из JSON-файла. ВСЕ текущие данные будут удалены."""
    if not yes:
        click.confirm(
            "Все текущие данные будут удалены и заменены данными из файла. Продолжить?",
            abort=True,
        )

    async def _():
        await _ensure_db()
        from app.db import engine
        from app.services.backup import restore_backup
        data = Path(file).read_bytes()
        stats = await restore_backup(engine, data)
        click.echo("Восстановление завершено:")
        for table, count in stats.items():
            click.echo(f"  {table}: {count} записей")
    run(_())


# -------------------------------------------------------------- admin

@cli.command("create-admin")
@click.argument("email")
@click.password_option(help="Пароль администратора")
def cmd_create_admin(email, password):
    """Создать администратора."""
    async def _():
        await _ensure_db()
        from app.db import AsyncSessionLocal
        from app.models import AdminUser
        from app.security import hash_password
        from sqlalchemy import select
        async with AsyncSessionLocal() as s:
            if (await s.execute(select(AdminUser).where(AdminUser.email == email))).scalar_one_or_none():
                click.echo(f"Администратор {email!r} уже существует.", err=True)
                sys.exit(1)
            s.add(AdminUser(email=email, password_hash=hash_password(password), is_active=True))
            await s.commit()
        click.echo(f"Администратор {email!r} создан.")
    run(_())


# ------------------------------------------------------------- clients

@cli.command("list-clients")
def cmd_list_clients():
    """Показать список клиентов."""
    async def _():
        await _ensure_db()
        from app.db import AsyncSessionLocal
        from app.models import Client, License
        from sqlalchemy import select, func
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(
                select(Client.id, Client.org_name, func.count(License.id).label("n"))
                .outerjoin(License, License.client_id == Client.id)
                .group_by(Client.id)
                .order_by(Client.id)
            )).all()
        if not rows:
            click.echo("Клиентов нет.")
            return
        click.echo(f"{'ID':>4}  {'Организация':<40}  Лицензий")
        click.echo("-" * 60)
        for r in rows:
            click.echo(f"{r.id:>4}  {r.org_name:<40}  {r.n}")
    run(_())


# --------------------------------------------------------------- db

@cli.command("db-init")
def cmd_db_init():
    """Создать все таблицы (если не существуют)."""
    async def _():
        await _ensure_db()
        click.echo("Таблицы созданы / проверены.")
    run(_())


@cli.command("sync-settings")
def cmd_sync_settings():
    """Синхронизировать настройки из config/*.cfg → таблицу app_settings."""
    async def _():
        await _ensure_db()
        from app.db import AsyncSessionLocal
        from app.services.settings_db import sync_from_config
        async with AsyncSessionLocal() as s:
            await sync_from_config(s)
        click.echo("Настройки синхронизированы.")
    run(_())


# ======================================================================

if __name__ == "__main__":
    cli()
