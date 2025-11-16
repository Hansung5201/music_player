from typing import Dict

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

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


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=TEST_ENGINE)
    Base.metadata.create_all(bind=TEST_ENGINE)
    yield


def login_host(client: TestClient) -> Dict:
    response = client.post("/auth/login", json={"name": "Host", "role": "host"})
    assert response.status_code == 200
    return response.json()


def create_session(client: TestClient, token: str) -> Dict:
    response = client.post(
        "/sessions",
        headers={"X-User-Token": token},
        json={"host_name": "Host"},
    )
    assert response.status_code == 200
    return response.json()


def join_guest(client: TestClient, code: str) -> Dict:
    response = client.post(f"/sessions/{code}/join", json={"guest_name": "Guest"})
    assert response.status_code == 200
    return response.json()


def test_guest_request_flow():
    client = TestClient(app)
    host = login_host(client)
    session = create_session(client, host["token"])
    guest = join_guest(client, session["code"])

    host_headers = {"X-User-Token": host["token"]}
    guest_headers = {"X-User-Token": guest["guest_token"]}

    add_resp = client.post(
        f"/sessions/{session['session_id']}/playlist",
        headers=host_headers,
        json={"track_id": "t1", "title": "Alpha", "artist": "Artist"},
    )
    assert add_resp.status_code == 200

    req_resp = client.post(
        f"/sessions/{session['session_id']}/playlist",
        headers=guest_headers,
        json={"track_id": "t2", "title": "Beta", "artist": "Artist"},
    )
    request = req_resp.json()
    assert request["status"] == "pending"

    approval = client.post(f"/requests/{request['id']}/approve", headers=host_headers, json={})
    assert approval.status_code == 200
    assert approval.json()["status"] == "approved"

    playlist = client.get(
        f"/sessions/{session['session_id']}/playlist",
        headers=host_headers,
    ).json()
    assert len(playlist) == 2
    assert playlist[1]["track_id"] == "t2"


def test_playback_updates_and_custom_request():
    client = TestClient(app)
    host = login_host(client)
    session = create_session(client, host["token"])
    guest = join_guest(client, session["code"])

    host_headers = {"X-User-Token": host["token"]}
    guest_headers = {"X-User-Token": guest["guest_token"]}

    client.post(
        f"/sessions/{session['session_id']}/playlist",
        headers=host_headers,
        json={"track_id": "song-1", "title": "Song", "artist": "Band"},
    )

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
    client.post(
        f"/sessions/{session['session_id']}/playlist",
        headers=host_headers,
        json={"track_id": "track-1", "title": "Song", "artist": "Artist"},
    )

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
                    "request_type": "add",
                    "payload": {"track_id": "track-2", "title": "Beta", "artist": "B"},
                },
            }
        )
        guest_request = guest_ws.receive_json()
        host_request = host_ws.receive_json()
        assert guest_request["type"] == host_request["type"] == "request_update"
        assert guest_request["payload"]["status"] == "pending"
