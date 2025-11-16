import base64
from pathlib import Path
from typing import Dict

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.database import Base, get_db
from app.main import app


TEST_ENGINE = create_engine(
    "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
)
TestingSessionLocal = sessionmaker(bind=TEST_ENGINE, autoflush=False, autocommit=False, future=True)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def setup_module(_):
    Base.metadata.create_all(bind=TEST_ENGINE)
    app.dependency_overrides[get_db] = override_get_db
    import app.main as main_module

    main_module.SessionLocal = TestingSessionLocal


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=TEST_ENGINE)
    Base.metadata.create_all(bind=TEST_ENGINE)
    yield


def login_host(client: TestClient) -> Dict:
    response = client.post("/auth/login", json={"name": "Host", "role": "host"})
    assert response.status_code == 200
    return response.json()


def create_session(client: TestClient, token: str, max_duration: int | None = None) -> Dict:
    payload = {"host_name": "Host"}
    if max_duration is not None:
        payload["max_media_duration_seconds"] = max_duration
    response = client.post(
        "/sessions",
        headers={"X-User-Token": token},
        json=payload,
    )
    assert response.status_code == 200
    return response.json()


def join_guest(client: TestClient, code: str) -> Dict:
    response = client.post(f"/sessions/{code}/join", json={"guest_name": "Guest"})
    assert response.status_code == 200
    return response.json()


def upload_track(
    client: TestClient,
    session_id: str,
    token: str,
    track_id: str,
    name: str,
    duration_seconds: int = 30,
) -> Dict:
    payload = {
        "track_id": track_id,
        "name": name,
        "duration_seconds": duration_seconds,
        "media": {
            "filename": f"{track_id}.mp3",
            "content_type": "audio/mpeg",
            "data": base64.b64encode(b"dummy-bytes").decode(),
        },
    }
    response = client.post(
        f"/sessions/{session_id}/playlist",
        headers={"X-User-Token": token},
        json=payload,
    )
    assert response.status_code == 200
    return response.json()


def test_guest_request_flow():
    client = TestClient(app)
    host = login_host(client)
    session = create_session(client, host["token"])
    guest = join_guest(client, session["code"])

    host_headers = {"X-User-Token": host["token"]}
    guest_headers = {"X-User-Token": guest["guest_token"]}

    upload_track(client, session["session_id"], host["token"], "t1", "Alpha")
    upload_track(client, session["session_id"], host["token"], "t2", "Beta")

    playlist = client.get(
        f"/sessions/{session['session_id']}/playlist",
        headers=host_headers,
    ).json()
    assert len(playlist) == 2
    second_item = next(item for item in playlist if item["track_id"] == "t2")

    req_resp = client.post(
        f"/sessions/{session['session_id']}/requests",
        headers=guest_headers,
        json={"request_type": "reorder", "payload": {"item_id": second_item["id"], "new_position": 0}},
    )
    request = req_resp.json()
    assert request["status"] == "pending"

    approval = client.post(f"/requests/{request['id']}/approve", headers=host_headers, json={})
    assert approval.status_code == 200
    assert approval.json()["status"] == "approved"

    updated = client.get(
        f"/sessions/{session['session_id']}/playlist",
        headers=host_headers,
    ).json()
    assert updated[0]["track_id"] == "t2"


def test_playback_updates_and_custom_request():
    client = TestClient(app)
    host = login_host(client)
    session = create_session(client, host["token"])
    guest = join_guest(client, session["code"])

    host_headers = {"X-User-Token": host["token"]}
    guest_headers = {"X-User-Token": guest["guest_token"]}

    upload_track(client, session["session_id"], host["token"], "song-1", "Song")

    playback = client.post(
        f"/sessions/{session['session_id']}/playback",
        headers=host_headers,
        json={"track_id": "song-1", "position_ms": 120000, "state": "playing"},
    )
    assert playback.status_code == 200
    assert playback.json()["position_ms"] == 120000

    custom = client.post(
        f"/sessions/{session['session_id']}/requests",
        headers=guest_headers,
        json={"request_type": "reorder", "payload": {"item_id": "invalid", "new_position": 0}},
    )
    assert custom.status_code == 200
    data = custom.json()
    assert data["status"] == "pending"

    denial = client.post(
        f"/requests/{data['id']}/deny",
        headers=host_headers,
        json={"reason": "Invalid track"},
    )
    assert denial.status_code == 200
    assert denial.json()["status"] == "denied"


def test_websocket_broadcast_flow():
    client = TestClient(app)
    host = login_host(client)
    session = create_session(client, host["token"])
    guest = join_guest(client, session["code"])

    host_headers = {"X-User-Token": host["token"]}
    upload_track(client, session["session_id"], host["token"], "track-1", "Song")
    playlist = client.get(
        f"/sessions/{session['session_id']}/playlist",
        headers=host_headers,
    ).json()
    first_item_id = playlist[0]["id"]

    with client.websocket_connect(
        f"/ws/sessions/{session['session_id']}?token={host['token']}"
    ) as host_ws, client.websocket_connect(
        f"/ws/sessions/{session['session_id']}?token={guest['guest_token']}"
    ) as guest_ws:
        host_state = host_ws.receive_json()
        host_playlist = host_ws.receive_json()
        guest_state = guest_ws.receive_json()
        guest_playlist = guest_ws.receive_json()

        assert host_state["type"] == guest_state["type"] == "playback_state"
        assert host_playlist["type"] == guest_playlist["type"] == "playlist_update"

        host_ws.send_json(
            {
                "type": "playback_command",
                "payload": {"action": "play", "track_id": "track-1", "position_ms": 0},
            }
        )
        guest_playback = guest_ws.receive_json()
        host_playback = host_ws.receive_json()
        assert guest_playback["type"] == host_playback["type"] == "playback_state"
        assert guest_playback["payload"]["state"] == "playing"

        guest_ws.send_json(
            {
                "type": "request_playlist_change",
                "payload": {
                    "request_type": "reorder",
                    "payload": {"item_id": first_item_id, "new_position": 0},
                },
            }
        )
        guest_request = guest_ws.receive_json()
        host_request = host_ws.receive_json()
        assert guest_request["type"] == host_request["type"] == "request_update"
        assert guest_request["payload"]["status"] == "pending"


def test_upload_respects_duration_limit():
    client = TestClient(app)
    host = login_host(client)
    session = create_session(client, host["token"], max_duration=10)

    response = client.post(
        f"/sessions/{session['session_id']}/playlist",
        headers={"X-User-Token": host["token"]},
        json={
            "track_id": "long",
            "name": "Long Track",
            "duration_seconds": 45,
            "media": {
                "filename": "long.mp3",
                "content_type": "audio/mpeg",
                "data": base64.b64encode(b"dummy").decode(),
            },
        },
    )
    assert response.status_code == 400
