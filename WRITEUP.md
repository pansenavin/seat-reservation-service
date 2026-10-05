# Seat Reservation at Scale — Engineering Notes

## Atomic decision and race safety

PostgreSQL is the source of truth. A reservation runs within
`transaction.atomic()`, acquires a transaction-scoped advisory lock for the
`(show, user)` pair before checking the configured per-user seat limit (default
four), then locks all
requested seat rows with `select_for_update()` in seat-number/primary-key
order. It checks availability and writes the reservation, seat links, and
confirmed seat states in the same transaction. Competing reservations for a
seat serialize on that row; the loser sees the committed state and receives a
409. The per-user advisory lock prevents concurrent requests from both
passing the limit check.

The deterministic seat-lock ordering avoids lock-order cycles for
multi-seat requests. Requests are all-or-nothing: if any requested seat is
missing, unavailable, or over the user's limit, none of the requested seats
are reserved. Database uniqueness constraints provide additional protection
for seat numbers within a show, idempotency keys within a user/show, and
reservation-seat pairs.

## Idempotency and cancellation

Set `MAX_CONFIRMED_SEATS_PER_USER_PER_SHOW` in the environment to change the
per-user seat cap; invalid or non-positive values prevent startup.

The idempotency key and SHA-256 hash of a canonicalized sorted seat list are
stored on the reservation. Repeating the same key and request returns the
existing reservation; reusing that key for a different body returns 409. A
unique database constraint on `(user_id, show_id, idempotency_key)` is the
durable backstop. A replay does not create another reservation or increment
the confirmed counter.

This implementation uses immediate confirmation rather than temporary holds.
The release model is explicit owner-only cancellation. Cancellation locks
the reservation, takes the same user/show advisory lock, then locks its seat
rows before transitioning the reservation and making those seats available
in one transaction. Repeating cancellation is safe.

## Consistency and availability

The application does not report a successful reservation without a
PostgreSQL commit. If PostgreSQL is unavailable, readiness returns 503 and
write requests fail rather than accepting reservations from a cache or local
state. This deliberately favors consistency over write availability during a
database partition. There is no claim of multi-region or failover availability
in this take-home implementation.

## Observability

The readiness endpoint checks PostgreSQL with `SELECT 1`; liveness does not
touch the database. Prometheus counters track new confirmations,
cancellations, and controlled decline/replay reasons. `seats_available` is
queried from PostgreSQL at scrape time so a process restart cannot leave that
gauge stale. The counters are process-local Prometheus counters and reset
when the process restarts; they are not an accounting ledger.

JSON stdout logs include request IDs, request lifecycle fields, and safe
reservation event fields. They omit user IDs, request bodies, bearer tokens,
and raw idempotency keys. In an operational deployment I would alert on
readiness failures, sustained 5xx, elevated reservation latency, and
unexpected differences between database seat counts and reported metrics.
Metrics are public for the local/demo assignment; production access should be
restricted at the ingress/network layer.

## AI usage

AI assistance was used to implement and revise application code, tests, the
burst script, and documentation based on the assignment requirements. The
requirements and scope were directed by the developer; generated changes
were reviewed and exercised with Django checks/tests and PostgreSQL-backed
concurrency tests. No performance result is claimed from the functional
burst script.

## Remaining work

- Deploy the image and PostgreSQL to a selected public platform, then verify
  cold start, readiness, metrics, and logs on the live URL.
- Run a separately provisioned, instrumented load test before making any
  20,000-request capacity claim.
- Integrate the organization's identity provider, token rotation/revocation,
  and rate limiting before exposing reservation tokens publicly.
- Add payment processing only with a durable outbox/payment state machine;
  this service currently records reservation amounts and does not charge.
- Add operational database backup/restore checks and capacity monitoring.
