from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import db_config


def _create_engine():
    url = db_config.url
    db_type = db_config.db_type
    kwargs: dict = {"future": True, "echo": db_config.echo_sql}

    if db_type == "sqlite":
        # NullPool предотвращает "Cannot reuse connection across tasks" в async-коде
        from sqlalchemy.pool import NullPool
        kwargs["poolclass"] = NullPool
    else:
        # Для postgres/mariadb используем pooling
        kwargs["pool_size"] = db_config.pool_size
        kwargs["max_overflow"] = db_config.max_overflow

    return create_async_engine(url, **kwargs)


engine = _create_engine()
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


async def get_session():
    async with AsyncSessionLocal() as session:
        yield session
