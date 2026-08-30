# Real-Time Data Analytics & Monitoring System

## Overview

A full-stack real-time monitoring system built with **FastAPI**, **Streamlit** and
**MariaDB 11.7**. It provides JWT-authenticated data management with role-based
access control, a live WebSocket data stream with charts and anomaly marking,
analytics over the persisted history, CSV/JSON bulk import, Excel export, and an
administration area with user management, audit logs and database status.

The whole system runs with a single `docker compose up --build`.

This is a take-home assignment: it is a complete, working system, but it is
scoped for a single-instance demonstration rather than production deployment
(see [Important design notes](#important-design-notes)).

## Features

**Authentication & RBAC**
- JWT bearer authentication (registration, login, current user)
- Three roles: `ADMIN`, `USER`, `VIEWER`
- Role-aware permissions enforced by the backend on every request

**Data management**
- Full CRUD on data records (title, value, category, timestamp)
- Pagination, category/source filtering, timestamp-range filtering, sorting
- Ownership rules: users manage their own records, admins manage any
- Bulk import from CSV and JSON (validated as a whole, inserted atomically)
- Excel (`.xlsx`) export of the filtered result set

**Real-time monitoring**
- Simulated reading generated once per second
- Authenticated WebSocket push to connected clients
- Live line chart and per-category bar chart
- Anomaly marking against a configurable threshold
- Batch persistence to MariaDB, triggered by batch size **or** elapsed interval

**Analytics**
- Total, average, minimum and maximum
- Time-range queries
- Per-category aggregation
- Trend series bucketed by minute, hour or day

**Administration** (Admin only)
- List all users
- Change role and activate/deactivate accounts
- Browse the audit log with filters and pagination
- Database status: health, connection pool, row counts, latest reading
- Persisted real-time history

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, Pydantic v2, PyJWT, bcrypt |
| Database access | SQLAlchemy 2.0 async ORM, asyncmy, Alembic |
| Database | MariaDB 11.7 |
| Frontend | Streamlit, pandas, `websockets` client, requests |
| Export | openpyxl |
| Infrastructure | Docker, multi-stage Dockerfiles, Docker Compose |

All database access goes through the SQLAlchemy ORM — the application contains
no raw SQL.

## Architecture

**System architecture diagram: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**
(Mermaid diagram plus component responsibilities, data model and data flows.)

In short: Streamlit talks to FastAPI over REST and WebSocket only and never
touches the database. FastAPI owns all persistence through the async ORM. A
background task generates one reading per second, broadcasts it immediately to
WebSocket subscribers, and appends it to an in-memory buffer that a separate
task flushes to MariaDB in batches, so delivery never waits on the database.

## Quick start

```bash
cp .env.example .env
docker compose up --build
```

`.env.example` ships with working demo values, so the stack starts as-is. For
anything beyond a local demo, replace the secrets first:

```bash
# generate a real JWT signing key
openssl rand -hex 32
```

On first start the backend applies the Alembic migrations, seeds the demo
accounts, then starts serving. MariaDB data persists in a named Docker volume
between restarts.

## Docker deployment

```bash
docker compose up --build      # build and start everything
docker compose ps              # check container health
docker compose logs -f backend # follow backend logs
docker compose down            # stop containers, KEEP the database
docker compose down -v         # stop containers and DELETE the database volume
```

> **Warning:** `docker compose down -v` permanently deletes the MariaDB volume,
> including all users, records and audit logs. Use plain `docker compose down`
> unless you intend to start from an empty database.

## Application URLs

| Service | URL |
|---|---|
| Streamlit UI | http://localhost:8501 |
| FastAPI backend | http://localhost:8000 |
| **API documentation (Swagger)** | **[http://localhost:8000/docs](http://localhost:8000/docs)** |
| OpenAPI schema | http://localhost:8000/openapi.json |
| Health check | http://localhost:8000/health |

Ports are configurable via `BACKEND_PORT` and `FRONTEND_PORT` in `.env`.

## Test accounts

Seeded automatically on first start from `.env`, one per role:

| Role | Username | Password | Purpose |
|---|---|---|---|
| ADMIN | `admin` | `demo-admin-pw` | Full access, including the Admin page |
| USER | `user` | `demo-user-pw` | Create/import records, manage own records |
| VIEWER | `viewer` | `demo-viewer-pw` | Read-only: records, analytics, realtime |

Sign in at http://localhost:8501.

> **These are local demonstration credentials only and must be changed for any
> non-demo environment.** The demo credentials are configured through
> environment variables. The values shown above are provided in `.env.example`
> for local demonstration only.

Notes:
- Seeding is idempotent: existing accounts are never modified, and passwords are
  never reset on restart. Leave a role's variables blank in `.env` to skip it.
- **Public self-registration always creates a `USER`.** There is no public way to
  obtain `ADMIN`; an existing admin promotes accounts from the Admin page.

## Sample data

[`samples/data_records_sample.csv`](samples/data_records_sample.csv) contains ten
records across four categories (temperature, pressure, voltage, humidity),
including two values above the default realtime anomaly threshold
(`ANOMALY_THRESHOLD`, 80.0) for representative high-value sample data.
Note that the anomaly *flag*
is server-controlled and applied to generated realtime readings only —
imported rows are stored with `is_anomaly = false` regardless of their value.

To import it: sign in as `admin` or `user` → **Records** → **Import** tab →
upload the file → **Import file**.

The import endpoint accepts **CSV and JSON**; both must use exactly the columns
`title,value,category,timestamp`. Ownership, provenance and the anomaly flag are
assigned by the server and cannot be set from the file.

## Role matrix

Enforced by the backend; the UI mirrors it for convenience only.

| Capability | ADMIN | USER | VIEWER |
|---|:---:|:---:|:---:|
| Read records, analytics, Excel export | ✅ | ✅ | ✅ |
| Realtime WebSocket stream | ✅ | ✅ | ✅ |
| Create records / bulk import | ✅ | ✅ | ❌ |
| Update / delete **own** records | ✅ | ✅ | ❌ |
| Update / delete **any** record | ✅ | ❌ | ❌ |
| Admin: users, roles, audit log, database status | ✅ | ❌ | ❌ |

Unauthenticated requests receive `401`; authenticated requests without the
required role receive `403`.

## Testing

Both suites run inside the built images, so no local Python setup is needed.
Build the images first (`docker compose build`).

```bash
# backend
docker run --rm --user root -v "$PWD/backend:/app" -w /app \
  --entrypoint sh realtime-monitoring-system-backend \
  -c "pip install -q -r requirements-dev.txt && pytest"

# frontend
docker run --rm --user root -v "$PWD/frontend:/app" -w /app \
  --entrypoint sh realtime-monitoring-system-frontend \
  -c "pip install -q -r requirements-dev.txt && pytest"
```

Backend tests use an isolated SQLite database and never touch MariaDB.

## Important design notes

- **One FastAPI worker.** The WebSocket connection manager and the realtime
  write buffer are in-process, so a second worker would run a second generator
  and split the broadcast set. Distributed fan-out is out of scope here.
- **Delivery is decoupled from persistence.** Each reading is broadcast
  immediately and buffered; the buffer flushes when it reaches `BATCH_SIZE` or
  `BATCH_INTERVAL_SECONDS` elapses, whichever comes first, and on shutdown. A
  slow database therefore cannot delay the live stream.
- **Two kinds of logs.** `audit_logs` is the queryable record of user and admin
  activity, exposed through the Admin page; operational application logs go to
  stdout and are read with `docker compose logs`.
- **Streamlit realtime client.** Streamlit re-runs its script on every
  interaction, so the WebSocket is consumed by a background thread that feeds a
  thread-safe queue; the page drains it on a one-second fragment refresh and
  keeps a bounded in-memory window (MariaDB holds the real history).
