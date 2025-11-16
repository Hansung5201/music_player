from __future__ import annotations

import asyncio
import base64
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

try:  # pragma: no cover - optional dependency
    from mutagen import File as MutagenFile
except Exception:  # pragma: no cover - fall back to hints
    MutagenFile = None
from sqlalchemy.orm import Session

from .database import SessionLocal, get_db, init_db
from .models import CollabSession, PlaylistRequestEntry, User
from .schemas import (
    CustomPlaylistRequest,
    JoinSessionRequest,
    JoinSessionResponse,
    LoginRequest,
    LoginResponse,
    MessageEnvelope,
    PlaybackCommand,
    PlaybackStateModel,
    PlaylistMutationRequest,
    PlaylistRequestModel,
    RequestResolution,
    SessionCreateRequest,
    SessionResponse,
)
from .services import (
    add_playlist_item,
    apply_request,
    build_request_log,
    build_request_model,
    build_session_response,
    create_request,
    create_token,
    ensure_session,
    ensure_session_membership,
    generate_code,
    remove_playlist_item,
    reorder_playlist,
    serialize_playback,
    serialize_playlist,
    update_playback_state,
)

init_db()

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_MEDIA_TYPES = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "video/mp4": ".mp4",
    "audio/mp4": ".mp4",
}
ALLOWED_MEDIA_EXTENSIONS = {".mp3", ".mp4"}

app = FastAPI(title="Collaborative Music Player")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

static_dir = BASE_DIR / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


class ConnectionManager:
    def __init__(self) -> None:
        self.active: Dict[str, List[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self.lock:
            self.active.setdefault(session_id, []).append(websocket)

    async def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            if session_id in self.active and websocket in self.active[session_id]:
                self.active[session_id].remove(websocket)

    async def broadcast(self, session_id: str, message: Dict) -> None:
        payload = json.dumps(message)
        async with self.lock:
            targets = list(self.active.get(session_id, []))
        for websocket in targets:
            try:
                await websocket.send_text(payload)
            except RuntimeError:
                pass


manager = ConnectionManager()


def resolve_media_extension(filename: str, content_type: str | None) -> str:
    content_type = (content_type or "").lower()
    if content_type in ALLOWED_MEDIA_TYPES:
        return ALLOWED_MEDIA_TYPES[content_type]
    suffix = Path(filename or "").suffix.lower()
    if suffix in ALLOWED_MEDIA_EXTENSIONS:
        return suffix
    raise HTTPException(status_code=415, detail="Only MP3 or MP4 uploads are supported")


def persist_media_bytes(content: bytes, extension: str) -> tuple[str, Path]:
    filename = f"{uuid4().hex}{extension}"
    destination = UPLOAD_DIR / filename
    with destination.open("wb") as buffer:
        buffer.write(content)
    relative_path = f"uploads/{filename}"
    return relative_path, destination


def detect_duration_seconds(file_path: Path) -> Optional[int]:
    if MutagenFile is None:
        return None
    try:
        media_file = MutagenFile(file_path)
    except Exception:
        return None
    if not media_file or not getattr(media_file, "info", None):
        return None
    length = getattr(media_file.info, "length", None)
    if length is None:
        return None
    return max(0, int(length))


async def get_actor(
    token: Optional[str] = Header(default=None, alias="X-User-Token"),
    db: Session = Depends(get_db),
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    actor = db.query(User).filter(User.token == token).one_or_none()
    if not actor:
        raise HTTPException(status_code=401, detail="invalid token")
    return actor


async def broadcast_playlist(session: CollabSession) -> None:
    await manager.broadcast(
        session.id,
        MessageEnvelope(type="playlist_update", payload={"playlist": serialize_playlist(session)}).model_dump(),
    )


async def broadcast_request_update(entry: PlaylistRequestEntry) -> None:
    await manager.broadcast(
        entry.session_id,
        MessageEnvelope(type="request_update", payload=build_request_model(entry).model_dump()).model_dump(),
    )


async def broadcast_playback(session: CollabSession) -> None:
    await manager.broadcast(
        session.id,
        MessageEnvelope(type="playback_state", payload=serialize_playback(session)).model_dump(),
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    token = create_token()
    user = User(name=payload.name, role=payload.role, token=token)
    db.add(user)
    db.commit()
    db.refresh(user)
    return LoginResponse(token=user.token, user_id=user.id, role=user.role)


@app.post("/sessions", response_model=SessionResponse)
def create_session(payload: SessionCreateRequest, actor: User = Depends(get_actor), db: Session = Depends(get_db)) -> SessionResponse:
    if actor.role != "host":
        raise HTTPException(status_code=403, detail="host token required")
    actor.name = payload.host_name
    session = CollabSession(
        code=generate_code(db),
        host_id=actor.id,
        max_media_duration_seconds=payload.max_media_duration_seconds,
    )
    actor.session = session
    db.add(session)
    db.commit()
    db.refresh(session)
    db.refresh(actor)
    return build_session_response(session, include_host_token=True)


@app.post("/sessions/{code}/join", response_model=JoinSessionResponse)
def join_session(code: str, payload: JoinSessionRequest, db: Session = Depends(get_db)) -> JoinSessionResponse:
    session = db.query(CollabSession).filter(CollabSession.code == code).one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    token = create_token()
    guest = User(name=payload.guest_name, role="guest", token=token, session_id=session.id)
    db.add(guest)
    db.commit()
    db.refresh(session)
    return JoinSessionResponse(
        session_id=session.id,
        guest_token=guest.token,
        max_media_duration_seconds=session.max_media_duration_seconds,
        playlist=serialize_playlist(session),
        playback_state=PlaybackStateModel(**serialize_playback(session)),
    )


@app.get("/sessions/{session_id}/playlist", response_model=List[Dict])
def get_playlist(session_id: str, actor: User = Depends(get_actor), db: Session = Depends(get_db)) -> List[Dict]:
    session = ensure_session_membership(db, actor, session_id)
    return serialize_playlist(session)


@app.post("/sessions/{session_id}/playlist", response_model=Dict)
async def add_playlist_item_endpoint(
    session_id: str,
    request: Request,
    actor: User = Depends(get_actor),
    db: Session = Depends(get_db),
):
    session = ensure_session_membership(db, actor, session_id)
    if actor.role == "host":
        try:
            payload_data = await request.json()
        except json.JSONDecodeError as exc:  # pragma: no cover - FastAPI already validates
            raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
        track_id = (payload_data.get("track_id") or "").strip()
        name = (payload_data.get("name") or "").strip()
        media_payload = payload_data.get("media") or {}
        encoded_media = media_payload.get("data")
        filename = media_payload.get("filename", "")
        media_type = media_payload.get("content_type")
        duration_hint = payload_data.get("duration_seconds")
        if not track_id or not name or not encoded_media:
            raise HTTPException(status_code=422, detail="track_id, name, and media data are required")
        duration_hint_value: Optional[int] = None
        if duration_hint not in (None, ""):
            try:
                duration_hint_value = max(0, int(duration_hint))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="duration_seconds must be a positive integer") from exc
        extension = resolve_media_extension(filename, media_type)
        media_path = ""
        destination: Optional[Path] = None
        try:
            try:
                binary = base64.b64decode(encoded_media)
            except Exception as exc:
                raise HTTPException(status_code=422, detail="media payload is not valid base64") from exc
            media_path, destination = persist_media_bytes(binary, extension)
            detected_duration = detect_duration_seconds(destination)
            duration_seconds = detected_duration or duration_hint_value
            if session.max_media_duration_seconds is not None:
                if duration_seconds is None:
                    raise HTTPException(status_code=422, detail="Unable to determine media duration for enforcement")
                if duration_seconds > session.max_media_duration_seconds:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Track is longer than the allowed {session.max_media_duration_seconds} seconds",
                    )
            item = add_playlist_item(
                db,
                session,
                track_id,
                name,
                media_path,
                media_type,
                duration_seconds,
            )
        except HTTPException:
            if destination and destination.exists():
                destination.unlink(missing_ok=True)
            raise
        except Exception as exc:
            if destination and destination.exists():
                destination.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Unable to store uploaded media") from exc
        db.refresh(session)
        await broadcast_playlist(session)
        return {
            "id": item.id,
            "track_id": item.track_id,
            "name": item.title,
            "media_url": f"/static/{item.media_path}",
            "media_type": item.media_type,
            "duration_seconds": item.duration_seconds,
            "position": item.position,
        }
    payload_data = await request.json()
    payload = PlaylistMutationRequest(**payload_data)
    request_entry = create_request(db, session, actor, "add", payload.model_dump(exclude_none=True))
    await broadcast_request_update(request_entry)
    return build_request_model(request_entry).model_dump()


@app.patch("/sessions/{session_id}/playlist/{item_id}", response_model=Dict)
async def reorder_playlist_endpoint(
    session_id: str,
    item_id: str,
    payload: PlaylistMutationRequest,
    actor: User = Depends(get_actor),
    db: Session = Depends(get_db),
):
    session = ensure_session_membership(db, actor, session_id)
    if payload.new_position is None:
        raise HTTPException(status_code=422, detail="new_position required")
    if actor.role == "host":
        reorder_playlist(db, session, item_id, payload.new_position)
        db.refresh(session)
        await broadcast_playlist(session)
        return {"status": "updated"}
    request = create_request(db, session, actor, "reorder", {"item_id": item_id, "new_position": payload.new_position})
    await broadcast_request_update(request)
    return build_request_model(request).model_dump()


@app.delete("/sessions/{session_id}/playlist/{item_id}", response_model=Dict)
async def remove_playlist_item_endpoint(
    session_id: str,
    item_id: str,
    actor: User = Depends(get_actor),
    db: Session = Depends(get_db),
):
    session = ensure_session_membership(db, actor, session_id)
    if actor.role == "host":
        remove_playlist_item(db, session, item_id)
        db.refresh(session)
        await broadcast_playlist(session)
        return {"status": "removed"}
    request = create_request(db, session, actor, "remove", {"item_id": item_id})
    await broadcast_request_update(request)
    return build_request_model(request).model_dump()


@app.post("/sessions/{session_id}/requests", response_model=PlaylistRequestModel)
async def submit_custom_request(
    session_id: str,
    payload: CustomPlaylistRequest,
    actor: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> PlaylistRequestModel:
    session = ensure_session_membership(db, actor, session_id)
    request = create_request(db, session, actor, payload.request_type, payload.payload)
    await broadcast_request_update(request)
    return build_request_model(request)


@app.post("/requests/{request_id}/approve", response_model=PlaylistRequestModel)
async def approve_request(
    request_id: str,
    resolution: RequestResolution,
    actor: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> PlaylistRequestModel:
    if actor.role != "host":
        raise HTTPException(status_code=403, detail="host privileges required")
    request = db.get(PlaylistRequestEntry, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="request not found")
    session = ensure_session(db, request.session_id)
    if session.host_id != actor.id:
        raise HTTPException(status_code=403, detail="wrong session")
    apply_request(db, session, request)
    request.status = "approved"
    request.reason = resolution.reason
    build_request_log(db, request, "approved", resolution.reason)
    db.commit()
    db.refresh(request)
    await broadcast_playlist(session)
    await broadcast_request_update(request)
    return build_request_model(request)


@app.post("/requests/{request_id}/deny", response_model=PlaylistRequestModel)
async def deny_request(
    request_id: str,
    resolution: RequestResolution,
    actor: User = Depends(get_actor),
    db: Session = Depends(get_db),
) -> PlaylistRequestModel:
    if actor.role != "host":
        raise HTTPException(status_code=403, detail="host privileges required")
    request = db.get(PlaylistRequestEntry, request_id)
    if not request:
        raise HTTPException(status_code=404, detail="request not found")
    session = ensure_session(db, request.session_id)
    if session.host_id != actor.id:
        raise HTTPException(status_code=403, detail="wrong session")
    request.status = "denied"
    request.reason = resolution.reason
    build_request_log(db, request, "denied", resolution.reason)
    db.commit()
    db.refresh(request)
    await broadcast_request_update(request)
    return build_request_model(request)


def update_playback_state(session: CollabSession, state_update: Dict) -> None:
    if "track_id" in state_update:
        session.playback_track_id = state_update["track_id"]
    if "position_ms" in state_update:
        session.playback_position_ms = state_update["position_ms"]
    if "state" in state_update:
        session.playback_state = state_update["state"]
    session.playback_updated_at = datetime.now(timezone.utc)


@app.post("/sessions/{session_id}/playback", response_model=Dict)
async def update_playback(
    session_id: str,
    state_update: Dict,
    actor: User = Depends(get_actor),
    db: Session = Depends(get_db),
):
    session = ensure_session_membership(db, actor, session_id)
    if actor.role != "host":
        raise HTTPException(status_code=403, detail="host privileges required")
    update_playback_state(session, state_update)
    db.commit()
    await broadcast_playback(session)
    return serialize_playback(session)


async def handle_websocket_message(
    db: Session,
    session: CollabSession,
    actor: User,
    envelope: MessageEnvelope,
) -> None:
    if envelope.type == "playback_command":
        if actor.role != "host":
            raise HTTPException(status_code=403, detail="host privileges required")
        command = PlaybackCommand(**envelope.payload)
        state_update: Dict[str, Optional[str]] = {}
        if command.action in {"play", "seek"} and command.track_id:
            state_update["track_id"] = command.track_id
        if command.position_ms is not None:
            state_update["position_ms"] = command.position_ms
        if command.action == "play":
            state_update["state"] = "playing"
        elif command.action == "pause":
            state_update["state"] = "paused"
        elif command.action == "seek":
            state_update["state"] = session.playback_state
        elif command.action in {"skip_next", "skip_prev"}:
            items = sorted(session.playlist_items, key=lambda entry: entry.position)
            if session.playback_track_id and items:
                try:
                    index = next(
                        idx for idx, entry in enumerate(items) if entry.track_id == session.playback_track_id
                    )
                except StopIteration:
                    index = 0
            else:
                index = 0
            if command.action == "skip_next" and items:
                index = min(index + 1, len(items) - 1)
            elif command.action == "skip_prev" and items:
                index = max(index - 1, 0)
            state_update["track_id"] = items[index].track_id if items else None
            state_update["position_ms"] = 0
            state_update["state"] = session.playback_state
        update_playback_state(session, state_update)
        db.commit()
        await broadcast_playback(session)
    elif envelope.type == "request_playlist_change":
        if actor.role != "guest":
            raise HTTPException(status_code=403, detail="guest privileges required")
        request = create_request(db, session, actor, envelope.payload["request_type"], envelope.payload["payload"])
        await broadcast_request_update(request)
    elif envelope.type in {"approve_request", "deny_request"}:
        if actor.role != "host":
            raise HTTPException(status_code=403, detail="host privileges required")
        request_id = envelope.payload.get("request_id")
        request = db.get(PlaylistRequestEntry, request_id)
        if not request:
            raise HTTPException(status_code=404, detail="request not found")
        if envelope.type == "approve_request":
            apply_request(db, session, request)
            request.status = "approved"
        else:
            request.status = "denied"
        request.reason = envelope.payload.get("reason")
        build_request_log(db, request, request.status, request.reason)
        db.commit()
        await broadcast_playlist(session)
        await broadcast_request_update(request)
    elif envelope.type == "sync_ack":
        pass
    else:
        raise HTTPException(status_code=400, detail="unsupported message type")


@app.websocket("/ws/sessions/{session_id}")
async def session_socket(websocket: WebSocket, session_id: str, token: str) -> None:
    db = SessionLocal()
    try:
        actor = db.query(User).filter(User.token == token).one_or_none()
        session = db.get(CollabSession, session_id)
        if not actor or not session:
            await websocket.close(code=4003)
            return
        try:
            ensure_session_membership(db, actor, session_id)
        except HTTPException:
            await websocket.close(code=4003)
            return
    finally:
        db.close()

    await manager.connect(session_id, websocket)
    try:
        db = SessionLocal()
        session = ensure_session(db, session_id)
        await websocket.send_text(
            json.dumps(MessageEnvelope(type="playback_state", payload=serialize_playback(session)).model_dump())
        )
        await websocket.send_text(
            json.dumps(MessageEnvelope(type="playlist_update", payload={"playlist": serialize_playlist(session)}).model_dump())
        )
        db.close()
        while True:
            message = await websocket.receive_text()
            envelope = MessageEnvelope(**json.loads(message))
            db = SessionLocal()
            session = ensure_session(db, session_id)
            actor = db.query(User).filter(User.token == token).one()
            await handle_websocket_message(db, session, actor, envelope)
            db.close()
    except WebSocketDisconnect:
        await manager.disconnect(session_id, websocket)
    finally:
        try:
            db.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
