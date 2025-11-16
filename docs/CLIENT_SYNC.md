# Client Synchronization Playbook

The backend publishes authoritative playback information for every session through the `/ws/sessions/{id}` WebSocket route. To
complete Step 6 of `PLAN.txt`, implement the following loop inside your host and guest clients:

1. **Bootstrap** – Authenticate/join through the REST endpoints (`POST /auth/login`, `POST /sessions`, `POST
   /sessions/{code}/join`) and open the WebSocket using the issued token.
2. **Initial state** – Consume the initial `playback_state` and `playlist_update` messages that arrive immediately after the
   socket connects, setting your audio element to the provided `track_id`, `position_ms`, and `state`.
3. **Continuous synchronization**
   - Whenever a new `playback_state` arrives, compute the elapsed time since `updated_at` and compare it with your local playback
     head. If drift exceeds ±200 ms, seek to `position_ms + elapsed_ms`; otherwise perform subtle rate adjustments to avoid
     audible jumps.
   - Refresh UI playlists when receiving `playlist_update` payloads; highlight pending/approved/denied requests upon
     `request_update` messages to give immediate feedback.
   - Emit `sync_ack` heartbeats every 10 seconds containing your local timestamp so the backend can log drift metrics.
4. **Host commands** – When the host triggers actions (play, pause, seek, skip), send `playback_command` envelopes. The backend
   rebroadcasts the resulting authoritative `playback_state` to all peers.
5. **Guest collaboration** – Guests send `request_playlist_change` envelopes describing the target action (`add`, `reorder`,
   `remove`). These become durable records in the SQL database and transition from `pending` to `approved`/`denied` when the host
   responds via REST or corresponding WebSocket commands.
6. **Periodic resync** – On an interval (10 seconds recommended), cross-check REST `GET /sessions/{id}/playlist` and recent
   `playback_state` messages to catch up after packet loss or temporary disconnections.

This workflow keeps every participant’s audio element aligned with the backend’s authoritative timeline while reflecting the
request lifecycle mandated by the collaboration plan.
