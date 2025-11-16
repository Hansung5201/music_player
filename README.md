# Collaborative Music Player

This repository implements the execution plan in `PLAN.txt` for a FastAPI-based collaborative music player. The current build
deploys every step except the explicit security hardening items (token expiry, TLS, rate limiting) from Step 7 of the plan.

## Requirements

Install Python 3.11+ and the dependencies listed in `requirements.txt` and `external_tools.txt`.

```
pip install -r requirements.txt
```

Install FFmpeg via your system package manager before attempting audio processing helpers.

## Running the stack

The API and WebSocket server use a single entry point:

```
python -m app.main
```

This launches a FastAPI-powered service exposing REST endpoints for authentication, session creation, playlist collaboration,
request approvals, playback control, and the WebSocket synchronization channel defined in `PLAN.txt`. The default runtime uses
a SQLite database stored in `music.db`; override `DATABASE_URL` to point at PostgreSQL or another SQLAlchemy-compatible
backend for production deployments.

### Built-in dashboard GUI

Visiting [http://localhost:8000/](http://localhost:8000/) after starting the server loads the lightweight dashboard bundled in
`app/templates/dashboard.html`. The page provides:

- Token generation for hosts and guests with clear copy/paste affordances.
- Session creation/join flows with real-time summaries of invite codes, WebSocket status, and playback state.
- Playlist controls for hosts plus a custom request composer for guests.
- Playback command buttons and a live activity log that mirrors REST + WebSocket traffic.

The dashboard relies entirely on the public API, so it doubles as both a GUI front end and a reference implementation for API
consumers.

## Database schema

The SQLAlchemy models cover users (hosts/guests), sessions, playlist items, pending requests, and request logs so that every
playlist mutation and approval path is durable. Running the module will automatically create the schema; the tests run against
an isolated in-memory SQLite database by overriding the `get_db` dependency.

## Client synchronization

WebSocket clients receive `playback_state`, `playlist_update`, and `request_update` envelopes. Hosts may publish
`playback_command` messages (`play`, `pause`, `seek`, `skip_next`, `skip_prev`) while guests use `request_playlist_change`
to submit collaboration intents. See `docs/CLIENT_SYNC.md` for the drift-correction algorithm and the expected client-side
player loop defined in Step 6 of the plan.

## Containerized deployment

To satisfy the deployment expectations in Step 3 of `PLAN.txt`, this repository includes a Dockerfile, docker-compose stack,
and Nginx reverse proxy configuration. See `docs/DEPLOYMENT.md` for step-by-step instructions on building the image, launching
the API + PostgreSQL + proxy trio, and overriding environment variables for production rollouts.

## Testing

```
pytest
```
