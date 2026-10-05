# Seat Reservation at Scale

A Django REST service for selling assigned seats. PostgreSQL is the system of
record for seat state, reservations, idempotency, and the per-user booking cap.

## Run locally

Create the development environment file and start the stack:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The API listens on `http://localhost:8000`; PostgreSQL and Adminer are also
available through Compose. Apply migrations in another terminal:

```sh
docker compose exec web python manage.py migrate
# Create a staff user for show creation; there is no Django admin website.
docker compose exec web python manage.py createsuperuser
```

For a clean local database, run migrations before using the API. The local
Compose service uses Django's development server. The Docker image itself runs
Gunicorn and serves collected static assets through WhiteNoise.

## API

Business endpoints are available at the assignment's root paths:

| Method | Path | Access |
| --- | --- | --- |
| `POST` | `/shows` | Admin only |
| `GET` | `/shows/{show_id}` | Public |
| `POST` | `/shows/{show_id}/reserve` | Token-authenticated user |
| `POST` | `/reservations/{reservation_id}/cancel` | Owning token-authenticated user |
| `GET` | `/api/health/live/` | Public; does not query PostgreSQL |
| `GET` | `/api/health/ready/` | Public; returns 503 when PostgreSQL is unavailable |
| `GET` | `/metrics` | Public demo endpoint; Prometheus text format |

### Authentication

Reservation identity is derived from Django REST Framework's database-backed
`TokenAuthentication`; `X-User-ID` is ignored and cannot impersonate another
user. Create a staff account with `createsuperuser`, then create its token:

```sh
docker compose exec web python manage.py drf_create_token <username>
```

Send the returned token as `Authorization: Token <token>`. Show creation
requires a staff user's token. Use HTTPS for token
authentication outside local development. Do not commit or print tokens.

Example reservation:

```http
POST /shows/1/reserve
Authorization: Token <token>
Idempotency-Key: request-abc
Content-Type: application/json

{"seats": ["A1", "A2"]}
```

The first successful request returns 201; an exact idempotent retry returns
the existing reservation with 200. Reusing a key for a different seat list
returns 409. Multi-seat requests are all-or-nothing. A user may have at most
four confirmed seats per show. Cancellation releases only the reservation's
seats and is idempotent. Configure the per-user cap using
`MAX_CONFIRMED_SEATS_PER_USER_PER_SHOW` in `.env`; it defaults to `4` and must
be a positive integer.

## Correctness and concurrency

Each reservation is committed in one PostgreSQL transaction. The service
locks requested seat rows in deterministic seat-number order with
`select_for_update()`. A transaction-scoped advisory lock serializes requests
for the same user/show while checking the four-seat limit. Database unique
constraints backstop seat numbering and idempotency. A conflict is a 409, not
a server error. The cancellation path locks the reservation and its seats
before releasing them.

## Health, metrics, and logs

Liveness is process-only. Readiness runs `SELECT 1` against PostgreSQL and
fails closed with 503. `/metrics` reports reservation and cancellation
counters, controlled decline-reason counters, and `seats_available`. The
available-seat gauge is recalculated from PostgreSQL on every scrape, avoiding
in-process gauge drift after restarts.

Every request gets an `X-Request-ID` (reusing a supplied value or generating a
UUID4), and the response returns that ID. Request lifecycle and reservation
events are emitted as JSON to stdout; request bodies, user IDs, auth tokens,
and idempotency keys are not logged. In Docker, inspect them with:

```sh
docker compose logs web
```

## Functional concurrency burst

The async `httpx` script checks hot-seat contention, concurrent idempotent
retries, same-key/different-body conflicts, the per-user limit, 5xx responses,
and show-count reconciliation. Use a fresh show and fresh users/tokens for
each run; the tests create reservations and do not reset database state.

Provide at least `CONCURRENCY + 3` distinct DRF tokens via `BURST_TOKENS`
(comma-separated). The first `CONCURRENCY` tokens are used for distinct
hot-seat users; three more are reserved for the idempotency, conflict, and
per-user-limit checks. The tokens must belong to fresh users for the limit
check to start from zero confirmed seats.

```powershell
$env:BURST_TOKENS = "<token-1>,<token-2>,...,<token-103>"
python scripts/burst.py --base-url http://localhost:8000 --show-id 1 --hot-seat A1 --concurrency 100
```

`BASE_URL`, `SHOW_ID`, `HOT_SEAT`, `CONCURRENCY`, and `BURST_TOKENS` may be
provided as environment defaults; command-line options override them. Tokens
are never printed by the script. Seats already occupied or otherwise
unavailable may cause a test to be reported as skipped. This is a functional
concurrency correctness test, **not** a 20,000-user load/performance test; it
does not prove the service can sustain 20,000 simultaneous connections.

## Deployment status

The image is configured for Gunicorn, environment-provided secrets/database
settings, and WhiteNoise static files. No public deployment URL is configured
in this repository yet; deploy the image with a managed PostgreSQL database,
set the production environment variables, run migrations, and then provide
the resulting URL to the reviewers.
