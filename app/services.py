from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .models import CollabSession, PlaylistItem, PlaylistRequestEntry, RequestLog, User
from .schemas import (
    PlaybackStateModel,
    PlaylistRequestModel,
    SessionResponse,
)


def serialize_playlist(session: CollabSession) -> List[Dict]:
    items = sorted(session.playlist_items, key=lambda item: item.position)
    return [
        {
            "id": item.id,
            "track_id": item.track_id,
            "name": item.title,
            "media_url": f"/static/{item.media_path.lstrip('/')}",
            "media_type": item.media_type,
            "duration_seconds": item.duration_seconds,
            "position": item.position,
        }
        for item in items
    ]


def serialize_playback(session: CollabSession) -> Dict:
    return {
        "track_id": session.playback_track_id,
        "position_ms": session.playback_position_ms,
        "state": session.playback_state,
        "updated_at": session.playback_updated_at.isoformat(),
    }


def build_session_response(session: CollabSession, include_host_token: bool = False) -> SessionResponse:
    return SessionResponse(
        session_id=session.id,
        code=session.code,
        host_token=session.host.token if include_host_token else None,
        max_media_duration_seconds=session.max_media_duration_seconds,
        playlist=serialize_playlist(session),
        playback_state=PlaybackStateModel(**serialize_playback(session)),
    )


def build_request_model(request: PlaylistRequestEntry) -> PlaylistRequestModel:
    return PlaylistRequestModel(
        id=request.id,
        session_id=request.session_id,
        requester=request.requester.name,
        request_type=request.request_type,
        payload=request.payload,
        status=request.status,
        reason=request.reason,
    )


def ensure_session(db: Session, session_id: str) -> CollabSession:
    session = db.get(CollabSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def ensure_session_membership(db: Session, actor: User, session_id: str) -> CollabSession:
    session = ensure_session(db, session_id)
    if actor.role == "host" and session.host_id == actor.id:
        return session
    if actor.role == "guest" and actor.session_id == session.id:
        return session
    raise HTTPException(status_code=403, detail="not a member of this session")


def generate_code(db: Session) -> str:
    while True:
        code = secrets.token_hex(3).upper()
        existing = db.query(CollabSession).filter(CollabSession.code == code).one_or_none()
        if not existing:
            return code


def create_token() -> str:
    return secrets.token_hex(16)


def add_playlist_item(
    db: Session,
    session: CollabSession,
    track_id: str,
    name: str,
    media_path: str,
    media_type: str,
    duration_seconds: Optional[int],
) -> PlaylistItem:
    position = len(session.playlist_items)
    item = PlaylistItem(
        session_id=session.id,
        track_id=track_id,
        title=name,
        media_path=media_path,
        media_type=media_type,
        duration_seconds=duration_seconds,
        position=position,
    )
    db.add(item)
    if session.playback_track_id is None:
        session.playback_track_id = track_id
    db.commit()
    db.refresh(session)
    db.refresh(item)
    return item


def reorder_playlist(db: Session, session: CollabSession, item_id: str, new_position: int) -> None:
    items = sorted(session.playlist_items, key=lambda entry: entry.position)
    if new_position < 0 or new_position >= len(items):
        raise HTTPException(status_code=400, detail="new position out of range")
    try:
        item = next(entry for entry in items if entry.id == item_id)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="item not found") from exc
    items.remove(item)
    items.insert(new_position, item)
    for index, entry in enumerate(items):
        entry.position = index
    db.commit()


def remove_playlist_item(db: Session, session: CollabSession, item_id: str) -> None:
    item = db.get(PlaylistItem, item_id)
    if not item or item.session_id != session.id:
        raise HTTPException(status_code=404, detail="item not found")
    db.delete(item)
    remaining = [entry for entry in session.playlist_items if entry.id != item_id]
    for index, entry in enumerate(sorted(remaining, key=lambda entry: entry.position)):
        entry.position = index
    if session.playback_track_id == item.track_id:
        session.playback_track_id = remaining[0].track_id if remaining else None
    db.commit()


def apply_request(db: Session, session: CollabSession, request: PlaylistRequestEntry) -> None:
    payload = request.payload
    if request.request_type == "add":
        add_playlist_item(
            db,
            session,
            payload["track_id"],
            payload["name"],
            payload["media_path"],
            payload["media_type"],
            payload.get("duration_seconds"),
        )
    elif request.request_type == "reorder":
        reorder_playlist(db, session, payload["item_id"], payload["new_position"])
    elif request.request_type == "remove":
        remove_playlist_item(db, session, payload["item_id"])
    else:
        raise HTTPException(status_code=400, detail="unknown request type")


def log_request_event(db: Session, entry: PlaylistRequestEntry, status: str, message: str | None = None) -> None:
    db.add(RequestLog(request_id=entry.id, status=status, message=message))


def create_request(
    db: Session,
    session: CollabSession,
    actor: User,
    request_type: str,
    payload: Dict,
) -> PlaylistRequestEntry:
    entry = PlaylistRequestEntry(
        session_id=session.id,
        requester_id=actor.id,
        request_type=request_type,
        payload=payload,
    )
    db.add(entry)
    db.flush()
    log_request_event(db, entry, "pending", "submitted")
    db.commit()
    db.refresh(entry)
    return entry


def build_request_log(db: Session, request: PlaylistRequestEntry, status: str, reason: str | None) -> None:
    log_request_event(db, request, status, reason)


def update_playback_state(session: CollabSession, state_update: Dict) -> None:
    if "track_id" in state_update:
        session.playback_track_id = state_update["track_id"]
    if "position_ms" in state_update:
        session.playback_position_ms = state_update["position_ms"]
    if "state" in state_update:
        session.playback_state = state_update["state"]
    session.playback_updated_at = datetime.now(timezone.utc)
