import uuid

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from trustmark.infra.db import Base


class RegistryRecord(Base):
    __tablename__ = "registry_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    transaction_hash: Mapped[str] = mapped_column(String(66), unique=True, nullable=False)
    issuer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
