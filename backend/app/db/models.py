"""SQLAlchemy ORM models for graph persistence (nodes and edges)."""

from __future__ import annotations

from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

try:
    from app.db.session import Base
except ImportError:
    from .session import Base


class DBNode(Base):
    """Database model for an entity node in the criminal network."""

    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    type: Mapped[str] = mapped_column(String, index=True)
    label: Mapped[str] = mapped_column(String)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    def __repr__(self) -> str:
        return f"<DBNode(id={self.id!r}, type={self.type!r}, label={self.label!r})>"


class DBEdge(Base):
    """Database model for a relationship edge in the criminal network."""

    __tablename__ = "edges"

    id: Mapped[str] = mapped_column(String, primary_key=True, index=True)
    source_id: Mapped[str] = mapped_column(String, ForeignKey("nodes.id"), index=True)
    target_id: Mapped[str] = mapped_column(String, ForeignKey("nodes.id"), index=True)
    type: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[str | None] = mapped_column(String, nullable=True, index=True, default=None)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    evidence_source: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    def __repr__(self) -> str:
        return (
            f"<DBEdge(id={self.id!r}, source={self.source_id!r}, "
            f"target={self.target_id!r}, type={self.type!r})>"
        )
