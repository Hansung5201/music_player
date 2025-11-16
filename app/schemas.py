from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class PlaybackStateModel(BaseModel):
    track_id: Optional[str]
    position_ms: int = Field(ge=0)
    state: str
    updated_at: datetime


class PlaylistItemModel(BaseModel):
    id: str
    track_id: str
    name: str
    media_url: str
    media_type: str
    duration_seconds: Optional[int]
    position: int


class LoginRequest(BaseModel):
    name: str = Field(min_length=1)
    role: str = Field(pattern="^(host|guest)$")


class LoginResponse(BaseModel):
    token: str
    user_id: str
    role: str


class SessionCreateRequest(BaseModel):
    host_name: str
    max_media_duration_seconds: Optional[int] = Field(default=None, gt=0)


class SessionResponse(BaseModel):
    session_id: str
    code: str
    host_token: Optional[str]
    max_media_duration_seconds: Optional[int]
    playlist: List[PlaylistItemModel]
    playback_state: PlaybackStateModel


class JoinSessionRequest(BaseModel):
    guest_name: str


class JoinSessionResponse(BaseModel):
    session_id: str
    guest_token: str
    max_media_duration_seconds: Optional[int]
    playlist: List[PlaylistItemModel]
    playback_state: PlaybackStateModel


class PlaylistMutationRequest(BaseModel):
    track_id: Optional[str] = None
    name: Optional[str] = None
    duration_seconds: Optional[int] = Field(default=None, gt=0)
    media_url: Optional[str] = None
    new_position: Optional[int] = Field(default=None, ge=0)


class PlaylistRequestModel(BaseModel):
    id: str
    session_id: str
    requester: str
    request_type: str
    payload: Dict
    status: str
    reason: Optional[str]


class RequestResolution(BaseModel):
    reason: Optional[str] = None


class CustomPlaylistRequest(BaseModel):
    request_type: str
    payload: Dict


class PlaybackCommand(BaseModel):
    action: str = Field(pattern="^(play|pause|seek|skip_next|skip_prev)$")
    track_id: Optional[str] = None
    position_ms: Optional[int] = Field(default=None, ge=0)


class MessageEnvelope(BaseModel):
    type: str
    payload: dict
