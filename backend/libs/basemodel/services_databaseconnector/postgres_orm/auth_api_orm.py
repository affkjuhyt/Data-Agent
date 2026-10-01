import datetime
import uuid
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.basemodel.services_databaseconnector.postgres_orm.base import Base


class APIKeyORM(Base):
    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_tenant", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_from: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SecretVaultORM(Base):
    __tablename__ = "secrets_vault"
    __table_args__ = (Index("ix_secrets_vault_tenant", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    key_name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_type: Mapped[str] = mapped_column(String(64), nullable=False)
    algorithm: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    realm: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    openbao_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    rotation_due_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_rotated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    rotations: Mapped[list["KeyRotationORM"]] = relationship(back_populates="secret")


class IPAllowlistORM(Base):
    __tablename__ = "ip_allowlists"
    __table_args__ = (
        Index("ix_ip_allowlists_tenant", "tenant_id"),
        Index("uq_ip_allowlists_tenant_cidr", "tenant_id", "cidr", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuthPlatformSettingORM(Base):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class KeyRotationORM(Base):
    __tablename__ = "key_rotations"
    __table_args__ = (Index("ix_key_rotations_secret", "secret_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    secret_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("secrets_vault.id"), nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    old_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    secret: Mapped["SecretVaultORM"] = relationship(back_populates="rotations")


class ComplianceSettingORM(Base):
    __tablename__ = "compliance_settings"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pii_masking: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    pii_types: Mapped[str] = mapped_column(Text, server_default="EMAIL,PHONE,CCCD,CMND,CREDIT_CARD,IP", nullable=False)
    residency_region: Mapped[str] = mapped_column(String(32), server_default="Asia-SE1", nullable=False)
    residency_standards: Mapped[str] = mapped_column(Text, server_default="GDPR", nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, server_default="365", nullable=False)
    retention_lock: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    zero_trust: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    audit_residency: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(128), server_default="", nullable=True)


class MicrosoftSSOApprovalORM(Base):
    __tablename__ = "microsoft_sso_approvals"
    __table_args__ = (Index("idx_ms_approvals_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True, server_default=text("gen_random_uuid()::text"))
    keycloak_user_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="pending", nullable=False)
    requested_role: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requested_tenant_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

