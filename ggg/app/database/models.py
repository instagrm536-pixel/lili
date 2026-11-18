from datetime import datetime, timezone
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

class Base(DeclarativeBase):
    pass


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


class DatingProfile(Base):
    __tablename__ = "dating_profiles"
    __table_args__ = (
        CheckConstraint(
            "age >= 18 AND age <= 100",
            name="ck_dating_profiles_adult_age",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(100))
    age: Mapped[int] = mapped_column(Integer)
    gender: Mapped[str] = mapped_column(String(16))
    username: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class DatingRequest(Base):
    __tablename__ = "dating_requests"
    __table_args__ = (
        UniqueConstraint(
            "sender_telegram_id",
            "recipient_telegram_id",
            name="uq_dating_request_pair",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'declined')",
            name="ck_dating_request_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sender_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    recipient_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class QRCollection(Base):
    __tablename__ = "qr_collections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'deleted')",
            name="ck_qr_collections_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True
    )
    owner_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")


class QRItem(Base):
    __tablename__ = "qr_items"
    __table_args__ = (
        CheckConstraint(
            "item_type IN ('text', 'link', 'image', 'video', 'document')",
            name="ck_qr_items_type",
        ),
        CheckConstraint("size >= 0", name="ck_qr_items_size"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    qr_id: Mapped[int] = mapped_column(
        ForeignKey("qr_collections.id", ondelete="CASCADE"), index=True
    )
    item_type: Mapped[str] = mapped_column(String(16))
    text_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    url_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(127), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sort_order: Mapped[int] = mapped_column(Integer)
