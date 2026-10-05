"""Database tables. All live in the ``hazard`` schema on Postgres (no schema on SQLite)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (JSON, Boolean, DateTime, ForeignKey, Integer, LargeBinary, MetaData,
                        String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "hazard"
Json = JSON().with_variant(JSONB(), "postgresql")
ROLES = ("viewer", "editor", "owner")          # ascending rights


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """ISO text with an explicit UTC offset. SQLite hands back naive datetimes; they are UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    session_version: Mapped[int] = mapped_column(Integer, default=0)   # raised on sign-out
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(ForeignKey(User.id))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list["ProjectMember"]] = relationship(cascade="all, delete-orphan", back_populates="project")
    datasets: Mapped[list["Dataset"]] = relationship(cascade="all, delete-orphan", back_populates="project")
    scenarios: Mapped[list["Scenario"]] = relationship(cascade="all, delete-orphan", back_populates="project")
    curves: Mapped[list["ClientCurve"]] = relationship(cascade="all, delete-orphan", back_populates="project")


class ProjectMember(Base):
    __tablename__ = "project_members"
    project_id: Mapped[int] = mapped_column(ForeignKey(Project.id, ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class Dataset(Base):
    """One uploaded debug zip (its parsed lgd_recovery data is stored as a blob)."""
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("project_id", "sha256", name="uq_dataset_project_sha"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey(Project.id, ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(100), default="")
    filename: Mapped[str] = mapped_column(String(300))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    meta: Mapped[dict] = mapped_column(Json, default=dict)
    profile: Mapped[dict] = mapped_column(Json, default=dict)
    blob_key: Mapped[str] = mapped_column(String(300))
    uploaded_by: Mapped[int] = mapped_column(ForeignKey(User.id))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="datasets")
    results: Mapped[list["Result"]] = relationship(cascade="all, delete-orphan", back_populates="dataset")
    overrides: Mapped[list["ScenarioOverride"]] = relationship(cascade="all, delete-orphan", back_populates="dataset")


CURVE_KINDS = ("shape", "applied")     # shape: used by method 3; applied: the client's curve, comparison only
CURVE_BASES = ("face", "outstanding")   # what the monthly rates are a share of


class ClientCurve(Base):
    """A curve uploaded to a project.

    kind "shape": a reference curve, the method 3 tail shape for zips whose curve label matches.
    kind "applied": the client's applied recovery curve, drawn on the charts for comparison.
    """
    __tablename__ = "client_curves"
    __table_args__ = (UniqueConstraint("project_id", "label", "kind", name="uq_curve_project_label_kind"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey(Project.id, ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), default="shape")
    basis: Mapped[str] = mapped_column(String(20), default="face")
    values: Mapped[list] = mapped_column(Json)
    source_filename: Mapped[str] = mapped_column(String(300), default="")
    uploaded_by: Mapped[int] = mapped_column(ForeignKey(User.id))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="curves")


class Scenario(Base):
    __tablename__ = "scenarios"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_scenario_project_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey(Project.id, ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict] = mapped_column(Json, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey(User.id))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="scenarios")
    overrides: Mapped[list["ScenarioOverride"]] = relationship(cascade="all, delete-orphan", back_populates="scenario")
    results: Mapped[list["Result"]] = relationship(cascade="all, delete-orphan", back_populates="scenario")


class ScenarioOverride(Base):
    """Per-zip parameter values that replace the scenario's values for that zip."""
    __tablename__ = "scenario_overrides"
    scenario_id: Mapped[int] = mapped_column(ForeignKey(Scenario.id, ondelete="CASCADE"), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey(Dataset.id, ondelete="CASCADE"), primary_key=True)
    params: Mapped[dict] = mapped_column(Json, default=dict)

    scenario: Mapped[Scenario] = relationship(back_populates="overrides")
    dataset: Mapped[Dataset] = relationship(back_populates="overrides")


class Result(Base):
    """The outcome of running one scenario on one zip."""
    __tablename__ = "results"
    scenario_id: Mapped[int] = mapped_column(ForeignKey(Scenario.id, ondelete="CASCADE"), primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey(Dataset.id, ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default="ok")        # ok | error
    error: Mapped[str] = mapped_column(Text, default="")
    stale: Mapped[bool] = mapped_column(Boolean, default=False)
    effective_params: Mapped[dict] = mapped_column(Json, default=dict)
    curve_label: Mapped[str] = mapped_column(String(100), default="")
    summary: Mapped[dict] = mapped_column(Json, default=dict)
    payload: Mapped[dict] = mapped_column(Json, default=dict)
    computed_by: Mapped[int] = mapped_column(ForeignKey(User.id))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    scenario: Mapped[Scenario] = relationship(back_populates="results")
    dataset: Mapped[Dataset] = relationship(back_populates="results")


class AgentLog(Base):
    """What the assistant was asked, what it replied and proposed, and whether it was applied."""
    __tablename__ = "agent_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey(Project.id, ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id))
    instruction: Mapped[str] = mapped_column(Text)
    reply: Mapped[str] = mapped_column(Text, default="")
    proposals: Mapped[list] = mapped_column(Json, default=list)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Blob(Base):
    """Binary storage inside the database (STORAGE_BACKEND=db)."""
    __tablename__ = "blobs"
    key: Mapped[str] = mapped_column(String(300), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
