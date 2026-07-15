from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class BuildProfile(Base):
    __tablename__ = "build_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    normalized_profile_json: Mapped[str] = mapped_column(Text)
    canonical_json: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    catalog_version: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BuildImage(Base):
    __tablename__ = "build_image"
    __table_args__ = (
        # Postgres: one non-deleted image row per profile_hash
        Index(
            "build_image_profile_hash_active_uidx",
            "profile_hash",
            unique=True,
            postgresql_where=text("status <> 'DELETED'"),
            sqlite_where=text("status <> 'DELETED'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_hash: Mapped[str] = mapped_column(String(64), index=True)
    image_repository: Mapped[str] = mapped_column(String(255))
    image_tag: Mapped[str] = mapped_column(String(255))
    image_digest: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    base_image_digest: Mapped[str] = mapped_column(String(128))
    windows_base: Mapped[str] = mapped_column(String(32), index=True)
    vs_generation: Mapped[str] = mapped_column(String(16))
    capability_profile_json: Mapped[str] = mapped_column(Text)
    validation_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    factory_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    catalog_version: Mapped[str] = mapped_column(String(64))
    hot: Mapped[bool] = mapped_column(Boolean, default=False)
    size_gib: Mapped[float] = mapped_column(Float, default=50.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    capabilities: Mapped[list[BuildImageCapability]] = relationship(back_populates="image")


class BuildImageCapability(Base):
    __tablename__ = "build_image_capability"
    __table_args__ = (
        UniqueConstraint(
            "build_image_id",
            "capability_key",
            "capability_version",
            name="uq_image_capability",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    build_image_id: Mapped[int] = mapped_column(ForeignKey("build_image.id"), index=True)
    capability_key: Mapped[str] = mapped_column(String(128), index=True)
    capability_version: Mapped[str] = mapped_column(String(64), default="")

    image: Mapped[BuildImage] = relationship(back_populates="capabilities")


class BuildRequest(Base):
    __tablename__ = "build_request"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repository: Mapped[str] = mapped_column(String(255))
    git_ref: Mapped[str] = mapped_column(String(255))
    resolved_commit: Mapped[str] = mapped_column(String(255))
    commit_resolution: Mapped[str] = mapped_column(String(32), default="placeholder")
    solution_path: Mapped[str] = mapped_column(String(512))
    configuration: Mapped[str] = mapped_column(String(64))
    platform: Mapped[str] = mapped_column(String(64))
    requested_profile_hash: Mapped[str] = mapped_column(String(64), index=True)
    matched_profile_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    match_type: Mapped[str] = mapped_column(String(32), default="PENDING")
    reuse_mode: Mapped[str] = mapped_column(String(32), default="preferCompatible")
    image_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nuget_mode: Mapped[str] = mapped_column(String(64))
    jenkins_job_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jenkins_queue_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jenkins_build_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(64), index=True)
    requested_by: Mapped[str] = mapped_column(String(128))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    environment_json: Mapped[str] = mapped_column(Text)
    provided_capabilities_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_capabilities_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    events: Mapped[list[BuildEvent]] = relationship(back_populates="request")


class BuildEvent(Base):
    __tablename__ = "build_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    build_request_id: Mapped[str] = mapped_column(ForeignKey("build_request.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    request: Mapped[BuildRequest] = relationship(back_populates="events")


def make_engine(database_url: str):
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, future=True, connect_args=connect_args)


def make_session_factory(database_url: str):
    engine = make_engine(database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)