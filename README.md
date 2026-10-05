# Paytm Seat Reservation

A Django REST API for creating shows, inspecting seat availability, and making
transaction-safe seat reservations.

## Technology Stack

- Python 3.12+
- Django 5.x and Django REST Framework
- PostgreSQL with psycopg
- Docker, Docker Compose, and Adminer

## Project Structure

```text
app/          Django settings and entry points
manage.py     Django management command
Dockerfile    Django development image
docker-compose.yml  Django, PostgreSQL, and Adminer services
```

## Local Development

Create the environment file from the example:

```powershell
Copy-Item .env.example .env
```

Start the project:

```sh
docker compose up --build
```

The Django development server is available at http://localhost:8000. Compose starts PostgreSQL and Adminer alongside it; Django connects to PostgreSQL at `db`, the Compose service name.

## Database Setup

Run Django migrations:

```sh
docker compose exec web python manage.py migrate
```

Create a Django admin superuser:

```sh
docker compose exec web python manage.py createsuperuser
```

## Endpoints

- Health check: `GET` http://localhost:8000/api/health/
- Liveness: `GET` http://localhost:8000/api/health/live/
- Readiness (checks PostgreSQL): `GET` http://localhost:8000/api/health/ready/
- Prometheus metrics: `GET` http://localhost:8000/metrics
- Create a show: `POST` http://localhost:8000/api/shows/
- Show details and seat counts: `GET` http://localhost:8000/api/shows/1/
- Reserve seats: `POST` http://localhost:8000/api/shows/1/reserve/
- Cancel a reservation: `POST` http://localhost:8000/api/reservations/10/cancel/
- Django admin: http://localhost:8000/admin/
- Adminer: http://localhost:8080/

To connect in Adminer, select **PostgreSQL**, use server `db`, username `postgres`, password `postgres`, and database `seat_reservation` (or the values configured in `.env`).

## Reservation API (Development)

For local testing, reservation requests temporarily identify the user with the
`X-User-ID` header. This is **not authentication**: clients can choose this
value, so do not expose this mechanism to untrusted users. Replace it with a
trusted token/JWT authentication class before deployment; the reservation
service accepts the resolved user ID independently of the header mechanism.

Reservation requests require an `Idempotency-Key` header and a JSON body
containing seat numbers, for example:

```http
POST /api/shows/1/reserve/
X-User-ID: 123
Idempotency-Key: request-abc
Content-Type: application/json

{"seats": ["A1", "A2"]}
```

An identical retry returns the original reservation with HTTP 200; the first
successful request returns HTTP 201. Reusing a key with a different seat list
returns HTTP 409. Each user may have at most four confirmed seats per show.
The reservation service runs in one PostgreSQL transaction, locks seats in
seat-number order, and uses a transaction-scoped PostgreSQL advisory lock for
each user/show pair so concurrent requests cannot bypass that limit.

Cancellation uses the same development `X-User-ID` header. Only the owner can
cancel a reservation; repeating cancellation is safe and returns the cancelled
reservation again. Cancellation locks the reservation and its linked seats
inside one transaction before releasing those seats.

## Health and Metrics

The liveness endpoint reports process health without contacting PostgreSQL.
The readiness endpoint runs `SELECT 1` and returns HTTP 503 if PostgreSQL is
unavailable.

The unauthenticated `/metrics` endpoint exposes Prometheus text format. Success
and cancellation counters are incremented only after the corresponding
database transaction commits. Decline reasons are limited to `seat_taken`,
`per_user_limit`, and `idempotent_replay`; an exact idempotent replay is counted
as a replayed/declined attempt, but not as another confirmed reservation.
`seats_available` is queried from PostgreSQL on every scrape rather than
maintained by in-process increments, so it remains accurate after process
restarts and tracks the database source of truth.

## Request IDs and JSON Logs

Every request reuses a non-empty `X-Request-ID` header or receives a UUID4.
The ID is attached to the Django request, returned in the response header,
and included in request lifecycle and reservation business-event logs. Logs
are compact JSON written to stdout for Docker log collection. Request logs
include method, path, status, and elapsed milliseconds; exception logs include
the exception type and traceback frames. Request bodies, authorization tokens,
user IDs, and idempotency keys are not logged.

## Functional Concurrency Burst Test

Install the project requirements, start the API, and run the asynchronous
functional concurrency checks against a show with enough **available seats**
for the selected tests:

```sh
python scripts/burst.py \
  --base-url http://localhost:8000 \
  --show-id 1 \
  --hot-seat A1 \
  --concurrency 100
```

`SHOW_ID` is required (or pass `--show-id`). `BASE_URL`, `HOT_SEAT`, `USER_ID`,
and `CONCURRENCY` can be supplied as environment defaults; corresponding
command-line options override them. The script uses one `httpx.AsyncClient`,
assigns each request a unique `X-Request-ID`, and reports 2xx/4xx/5xx totals
and latency percentiles. Tests that lack the required available seats are
reported as skipped. Any 5xx or unexpected transport error fails the run.

The suite checks hot-seat no-double-selling, simultaneous idempotent retries,
same-key/different-body conflict handling, the per-user seat limit under
concurrent requests, and database-backed show count reconciliation. This is a
functional concurrency correctness test, **not** a 20,000-user concurrent load
or performance test; do not present it as one.