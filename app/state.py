from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4


@dataclass
class PlaybackState:
    track_id: Optional[str] = None
    position_ms: int = 0
    state: str = "paused"
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class PlaylistItem:
    id: str
    track_id: str
    title: str
    artist: str
    position: int


@dataclass
class PlaylistRequest:
    id: str
    session_id: str
    requester: str
    request_type: str
    payload: Dict
    status: str = "pending"
    reason: Optional[str] = None


@dataclass
class Session:
    id: str
    code: str
    host_name: str
    host_token: str
    playback: PlaybackState = field(default_factory=PlaybackState)
    playlist: List[PlaylistItem] = field(default_factory=list)
    requests: List[str] = field(default_factory=list)


@dataclass
class Actor:
    token: str
    name: str
    role: str  # "host" or "guest"
    session_id: str


class State:
    """In-memory representation of sessions, tokens, and requests."""

    def __init__(self) -> None:
        self.sessions: Dict[str, Session] = {}
        self.tokens: Dict[str, Actor] = {}
        self.requests: Dict[str, PlaylistRequest] = {}
        self.lock = asyncio.Lock()

    def _generate_code(self) -> str:
        return uuid4().hex[:6].upper()

    def _generate_token(self) -> str:
        return uuid4().hex

    def create_session(self, host_name: str) -> Session:
        session_id = uuid4().hex
        session = Session(
            id=session_id,
            code=self._generate_code(),
            host_name=host_name,
            host_token=self._generate_token(),
        )
        self.sessions[session_id] = session
        self.tokens[session.host_token] = Actor(
            token=session.host_token,
            name=host_name,
            role="host",
            session_id=session_id,
        )
        return session

    def add_guest(self, session: Session, guest_name: str) -> Actor:
        token = self._generate_token()
        actor = Actor(token=token, name=guest_name, role="guest", session_id=session.id)
        self.tokens[token] = actor
        return actor

    def get_session(self, session_id: str) -> Session:
        if session_id not in self.sessions:
            raise KeyError("session not found")
        return self.sessions[session_id]

    def find_session_by_code(self, code: str) -> Session:
        for session in self.sessions.values():
            if session.code == code:
                return session
        raise KeyError("session not found")

    def get_actor(self, token: str) -> Actor:
        if token not in self.tokens:
            raise KeyError("token not found")
        return self.tokens[token]

    def add_playlist_item(self, session: Session, track_id: str, title: str, artist: str) -> PlaylistItem:
        item = PlaylistItem(
            id=uuid4().hex,
            track_id=track_id,
            title=title,
            artist=artist,
            position=len(session.playlist),
        )
        session.playlist.append(item)
        return item

    def reorder_playlist(self, session: Session, item_id: str, new_position: int) -> None:
        playlist = session.playlist
        if new_position < 0 or new_position >= len(playlist):
            raise ValueError("new position out of bounds")
        for index, item in enumerate(playlist):
            if item.id == item_id:
                playlist.pop(index)
                playlist.insert(new_position, item)
                for idx, entry in enumerate(playlist):
                    entry.position = idx
                return
        raise KeyError("item not found")

    def remove_playlist_item(self, session: Session, item_id: str) -> PlaylistItem:
        playlist = session.playlist
        for index, item in enumerate(playlist):
            if item.id == item_id:
                playlist.pop(index)
                for idx, entry in enumerate(playlist):
                    entry.position = idx
                return item
        raise KeyError("item not found")

    def create_request(
        self,
        session: Session,
        requester: Actor,
        request_type: str,
        payload: Dict,
    ) -> PlaylistRequest:
        request = PlaylistRequest(
            id=uuid4().hex,
            session_id=session.id,
            requester=requester.name,
            request_type=request_type,
            payload=payload,
        )
        self.requests[request.id] = request
        session.requests.append(request.id)
        return request

    def update_request(self, request_id: str, status: str, reason: Optional[str] = None) -> PlaylistRequest:
        request = self.requests.get(request_id)
        if not request:
            raise KeyError("request not found")
        request.status = status
        request.reason = reason
        return request

    async def reset(self) -> None:
        async with self.lock:
            self.sessions.clear()
            self.tokens.clear()
            self.requests.clear()


state = State()
