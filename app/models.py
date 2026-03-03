import datetime as dt
from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean, Text, LargeBinary, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db import Base


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String(32), default="admin")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_name: Mapped[str] = mapped_column(String(255), index=True)
    notes: Mapped[str | None] = mapped_column(String(1024))
    logo_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    logo_mime: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    login: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_keys: Mapped[int] = mapped_column(Integer, default=5)
    key_ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id"), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # Soft-delete: при удалении org ставится метка, данные не уничтожаются
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    licenses: Mapped[list["License"]] = relationship(back_populates="client")


# Статусы лицензии (явное поле в БД):
# not_activated — новая, ни разу не активировалась
# activated     — активна на устройстве
# released      — была активирована, затем деактивирована (свободна)
# blocked       — заблокирована владельцем
# (expired — вычисляется на лету по expires_at, не хранится)
LICENSE_STATUSES = ("not_activated", "activated", "released", "blocked")


class License(Base):
    __tablename__ = "licenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    # Явный статус; expired вычисляется по expires_at
    status: Mapped[str] = mapped_column(String(20), default="not_activated")
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    block_reason: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    device_id: Mapped[str | None] = mapped_column(String(128), index=True)
    device_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    device_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    activation_payload: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str] = mapped_column(String(255), default="автоматическая генерация")
    client: Mapped["Client"] = relationship(back_populates="licenses")
    keys: Mapped[list["LicenseKey"]] = relationship(
        "LicenseKey",
        back_populates="license",
        cascade="all, delete-orphan",
        order_by="desc(LicenseKey.issued_at)",
    )
    actions: Mapped[list["LicenseAction"]] = relationship(
        "LicenseAction",
        back_populates="license",
        cascade="all, delete-orphan",
    )

    def computed_status(self, now: dt.datetime | None = None) -> str:
        """Вычисляет итоговый статус с учётом expires_at."""
        now = now or dt.datetime.utcnow()
        if self.is_blocked:
            return "blocked"
        if self.expires_at and now > self.expires_at:
            return "expired"
        return self.status


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
    action: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text)
    desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    license: Mapped["License"] = relationship(back_populates="actions")


class AppSetting(Base):
    """Хранилище настроек приложения в БД — участвует в резервных копиях."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(512))
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    ip_address: Mapped[str] = mapped_column(String(45))
    login: Mapped[str] = mapped_column(String(255))
    success: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)

    __table_args__ = (
        Index("ix_login_attempts_ip_at", "ip_address", "at"),
        Index("ix_login_attempts_login_at", "login", "at"),
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int] = mapped_column(Integer)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    ip_address: Mapped[str] = mapped_column(String(45))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    org_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="new")
    admin_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    messages: Mapped[list["FeedbackMessage"]] = relationship(
        "FeedbackMessage",
        back_populates="feedback",
        order_by="FeedbackMessage.created_at",
        cascade="all, delete-orphan",
    )


class FeedbackMessage(Base):
    """Сообщения диалога обратной связи (owner ↔ org)."""
    __tablename__ = "feedback_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    feedback_id: Mapped[int] = mapped_column(ForeignKey("feedback.id"), index=True)
    # sender_type: "admin" (ответ owner), "org" (ответ организации), "system" (исходное сообщение)
    sender_type: Mapped[str] = mapped_column(String(32))
    sender_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=dt.datetime.utcnow)
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback: Mapped["Feedback"] = relationship(back_populates="messages")
