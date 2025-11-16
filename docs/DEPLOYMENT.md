# Containerized deployment

This project ships with a Docker-based stack that satisfies Step 3 of `PLAN.txt` by packaging the FastAPI backend, a PostgreSQL
store, and an HTTP reverse proxy.

## Building the image

```bash
docker build -t collaborative-music-player .
```

## Running the stack

Use docker-compose to run the database, API server, and Nginx reverse proxy:

```bash
docker compose up --build
```

Services:

- `db`: PostgreSQL 15 storing users, sessions, playlists, requests, and logs.
- `api`: FastAPI application served by Uvicorn. It uses `DATABASE_URL` to connect to Postgres (defaults defined in
  `docker-compose.yml`).
- `nginx`: Reverse proxy that forwards HTTP and WebSocket traffic to the API container, preparing the stack for a TLS
  terminator when the security work in Step 7 begins.

The API is reachable on `http://localhost:8000`, while the reverse proxy exposes port `8080`. Adjust the compose file to bind
custom ports or to inject production-grade TLS certificates.

## Environment overrides

Override the default Postgres credentials by setting `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` inside an
`.env` file that docker-compose automatically loads, and update `DATABASE_URL` to match. For managed databases, remove the
`db` service and point `DATABASE_URL` to the remote host.

## Applying migrations

The application uses SQLAlchemy’s declarative metadata to create tables at startup. For production deployments, integrate Alembic
migrations and run them in the container entrypoint before launching Uvicorn.
