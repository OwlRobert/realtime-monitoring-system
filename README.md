# Realtime Data Analytics & Monitoring System

Monorepo containing a FastAPI backend, a Streamlit frontend and MariaDB 11.7,
orchestrated with Docker Compose. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

| Service      | URL                          |
| ------------ | ---------------------------- |
| Streamlit UI | http://localhost:8501        |
| Swagger docs | http://localhost:8000/docs   |
| Health check | http://localhost:8000/health |

Stop with `docker compose down`, or `docker compose down -v` to drop the database volume.

## Layout

```
backend/     FastAPI service — the only component that talks to MariaDB
  app/       application package (api, core, db, models, schemas)
  alembic/   migrations, applied before the API process starts
frontend/    Streamlit service — reaches the backend over REST, with WebSocket support planned for realtime streaming
docs/        architecture and production notes
```

## Status

**Phase 1 (complete)** — service skeleton: containers, networking, database
connectivity, Alembic wiring, `/health`, and a Streamlit status page.

Next phases: authentication and RBAC, data CRUD and import, realtime
WebSocket streaming, analytics and export, administration.
