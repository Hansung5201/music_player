from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from .database import Base


def generate_id() -> str:
    return uuid4().hex


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=generate_id)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    token = Column(String, unique=True, nullable=False, index=True)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=True)

    session = relationship("CollabSession", back_populates="participants", foreign_keys=[session_id])


class CollabSession(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True, default=generate_id)
    code = Column(String, unique=True, index=True, nullable=False)
    host_id = Column(String, ForeignKey("users.id"), nullable=False)
    playback_track_id = Column(String, nullable=True)
    playback_position_ms = Column(Integer, default=0)
    playback_state = Column(String, default="paused")
    playback_updated_at = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    host = relationship("User", foreign_keys=[host_id])
    participants = relationship("User", back_populates="session", foreign_keys="User.session_id")
    playlist_items = relationship(
        "PlaylistItem",
        order_by="PlaylistItem.position",
        cascade="all, delete-orphan",
        back_populates="session",
    )
    requests = relationship(
        "PlaylistRequestEntry",
        cascade="all, delete-orphan",
        back_populates="session",
        order_by="PlaylistRequestEntry.created_at",
    )


class PlaylistItem(Base):
    __tablename__ = "playlist_items"

    id = Column(String, primary_key=True, default=generate_id)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False, index=True)
    track_id = Column(String, nullable=False)
    title = Column(String, nullable=False)
    artist = Column(String, nullable=False)
    position = Column(Integer, nullable=False)

    session = relationship("CollabSession", back_populates="playlist_items")


class PlaylistRequestEntry(Base):
    __tablename__ = "playlist_requests"

    id = Column(String, primary_key=True, default=generate_id)
    session_id = Column(String, ForeignKey("sessions.id"), nullable=False, index=True)
    requester_id = Column(String, ForeignKey("users.id"), nullable=False)
    request_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String, default="pending", nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    session = relationship("CollabSession", back_populates="requests")
    requester = relationship("User")


class RequestLog(Base):
    __tablename__ = "request_logs"

    id = Column(String, primary_key=True, default=generate_id)
    request_id = Column(String, ForeignKey("playlist_requests.id"), nullable=False)
    status = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    request = relationship("PlaylistRequestEntry")
