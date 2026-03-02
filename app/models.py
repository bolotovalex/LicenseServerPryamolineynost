import datetime as dt
from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean, Text, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base

class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)

class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_name: Mapped[str] = mapped_column(String(255), index=True)
    notes: Mapped[str | None] = mapped_column(String(1024))
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary)   # бинарные данные логотипа
    logo_mime: Mapped[str | None] = mapped_column(String(50))       # image/png, image/jpeg, …
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    licenses: Mapped[list["License"]] = relationship(back_populates="client")

class License(Base):
    __tablename__ = "licenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    # текущий активный ключ (для быстрого поиска при активации)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    issued_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[str | None] = mapped_column(Text)

    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    device_id: Mapped[str | None] = mapped_column(String(128), index=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    activation_payload: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(String(255))

    client: Mapped["Client"] = relationship(back_populates="licenses")
    keys: Mapped[list["LicenseKey"]] = relationship(
        "LicenseKey",
        back_populates="license",
        cascade="all, delete-orphan",
        order_by="desc(LicenseKey.issued_at)",
    )

class LicenseKey(Base):
    __tablename__ = "license_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    license_id: Mapped[int] = mapped_column(ForeignKey("licenses.id"), index=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    deactivated_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    reason: Mapped[str | None] = mapped_column(Text)

    license: Mapped["License"] = relationship(back_populates="keys")

class LicenseAction(Base):
    __tablename__ = "license_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    license_id: Mapped[int] = mapped_column(ForeignKey("licenses.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))  # issue/reset/block/unblock/activate
    reason: Mapped[str | None] = mapped_column(Text)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

class AppSetting(Base):
    """Хранилище настроек приложения в БД — участвует в резервных копиях."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(512))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
