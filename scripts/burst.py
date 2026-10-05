#!/usr/bin/env python3
"""Functional async concurrency checks for the seat reservation API."""

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
import os
import statistics
import sys
import uuid

import httpx


@dataclass
class RequestResult:
    status_code: int | None
    reservation_id: int | None
    request_id: str
    elapsed_ms: float
    error: str | None = None
    response_data: dict | None = None


@dataclass
class TestOutcome:
    name: str
    state: str
    passed: bool
    results: list[RequestResult]
    details: list[str]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run functional async concurrency checks against a show."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "http://localhost:8000"),
    )
    parser.add_argument(
        "--show-id",
        type=int,
        default=int(os.environ["SHOW_ID"]) if os.getenv("SHOW_ID") else None,
        required=os.getenv("SHOW_ID") is None,
    )
    parser.add_argument(
        "--hot-seat",
        default=os.getenv("HOT_SEAT", "A1"),
    )
    parser.add_argument(
        "--tokens",
        default=os.getenv("BURST_TOKENS", ""),
        help=(
            "Comma-separated DRF token keys for fresh users. Supply at least "
            "CONCURRENCY + 3 distinct tokens."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("CONCURRENCY", "100")),
    )
    args = parser.parse_args()

    if args.show_id <= 0:
        parser.error("--show-id must be a positive integer")
    if args.concurrency < 2:
        parser.error("--concurrency must be at least 2")
    args.tokens = [token.strip() for token in args.tokens.split(",") if token.strip()]
    if len(args.tokens) < args.concurrency + 3:
        parser.error(
            "--tokens/BURST_TOKENS must contain at least "
            f"{args.concurrency + 3} distinct token keys"
        )
    if len(args.tokens) != len(set(args.tokens)):
        parser.error("each supplied token must be unique")
    if not args.hot_seat:
        parser.error("--hot-seat must not be empty")

    args.base_url = args.base_url.rstrip("/")
    return args


class BurstRunner:
    def __init__(self, args):
        self.args = args
        self.client = httpx.AsyncClient(
            base_url=args.base_url,
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(
                max_connections=min(max(args.concurrency, 20), 200),
                max_keepalive_connections=min(max(args.concurrency // 2, 10), 50),
            ),
        )
        self.outcomes: list[TestOutcome] = []
        self.all_results: list[RequestResult] = []

    async def close(self):
        await self.client.aclose()

    async def request(self, method, path, *, token=None, key=None, body=None):
        request_id = str(uuid.uuid4())
        headers = {"X-Request-ID": request_id}
        if token is not None:
            headers["Authorization"] = f"Token {token}"
        if key is not None:
            headers["Idempotency-Key"] = key

        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            response = await self.client.request(
                method,
                path,
                headers=headers,
                json=body,
            )
            elapsed_ms = (loop.time() - started) * 1000
            try:
                response_data = response.json()
            except ValueError:
                response_data = None
            reservation_id = (
                response_data.get("reservation_id")
                if isinstance(response_data, dict)
                else None
            )
            result = RequestResult(
                status_code=response.status_code,
                reservation_id=reservation_id,
                request_id=request_id,
                elapsed_ms=elapsed_ms,
                error=(
                    f"HTTP {response.status_code}"
                    if response.status_code >= 500
                    else None
                ),
                response_data=(
                    response_data if isinstance(response_data, dict) else None
                ),
            )
        except httpx.RequestError as error:
            result = RequestResult(
                status_code=None,
                reservation_id=None,
                request_id=request_id,
                elapsed_ms=(loop.time() - started) * 1000,
                error=type(error).__name__,
            )
        self.all_results.append(result)
        return result

    async def reserve(self, *, seat, token, key):
        return await self.request(
            "POST",
            f"/shows/{self.args.show_id}/reserve",
            token=token,
            key=key,
            body={"seats": [seat]},
        )

    async def fetch_show(self):
        return await self.request(
            "GET",
            f"/shows/{self.args.show_id}",
        )

    def add_outcome(self, outcome):
        self.outcomes.append(outcome)
        print(f"\n{outcome.name.upper()}")
        print("-" * max(len(outcome.name), 8))
        if outcome.state == "SKIPPED":
            print("SKIPPED")
            for detail in outcome.details:
                print(f"Reason: {detail}")
            return

        counts = Counter()
        for result in outcome.results:
            if result.status_code is None:
                counts["other"] += 1
            elif 200 <= result.status_code < 300:
                counts["2xx"] += 1
            elif 400 <= result.status_code < 500:
                counts["4xx"] += 1
            elif 500 <= result.status_code < 600:
                counts["5xx"] += 1
            else:
                counts["other"] += 1
        if outcome.results:
            print(f"2xx responses : {counts['2xx']}")
            print(f"4xx responses : {counts['4xx']}")
            print(f"5xx responses : {counts['5xx']}")
            print(f"Other/errors  : {counts['other']}")
            self.print_latency(outcome.results)
        for detail in outcome.details:
            print(detail)
        if counts["5xx"] or counts["other"]:
            for result in outcome.results:
                if result.status_code is None or (
                    result.status_code is not None
                    and result.status_code >= 500
                ):
                    print(
                        "Unexpected request failure: "
                        f"status={result.status_code}, "
                        f"request_id={result.request_id}, "
                        f"error={result.error or 'none'}"
                    )
        elif not outcome.passed:
            for result in outcome.results:
                if result.status_code is not None and result.status_code >= 400:
                    print(
                        "Unexpected request response: "
                        f"status={result.status_code}, "
                        f"request_id={result.request_id}"
                    )
        print(f"{outcome.state}: {outcome.name}")

    @staticmethod
    def print_latency(results):
        latencies = sorted(result.elapsed_ms for result in results)

        def percentile(percent):
            index = max(0, int((len(latencies) - 1) * percent))
            return latencies[index]

        print(f"Latency min   : {latencies[0]:.2f} ms")
        print(f"Latency max   : {latencies[-1]:.2f} ms")
        print(f"Latency avg   : {statistics.fmean(latencies):.2f} ms")
        print(f"Latency p50   : {percentile(0.50):.2f} ms")
        print(f"Latency p95   : {percentile(0.95):.2f} ms")
        print(f"Latency p99   : {percentile(0.99):.2f} ms")

    @staticmethod
    def skipped(name, reason):
        return TestOutcome(name, "SKIPPED", True, [], [reason])

    @staticmethod
    def result_5xx_count(outcomes):
        return sum(
            1
            for outcome in outcomes
            for result in outcome.results
            if result.status_code is not None and result.status_code >= 500
        )

    async def run_hot_seat(self, available):
        name = "Hot seat concurrency"
        if self.args.hot_seat not in available:
            outcome = self.skipped(
                name,
                f"Hot seat {self.args.hot_seat!r} does not exist or is not available.",
            )
            self.add_outcome(outcome)
            return outcome

        requests = [
            self.reserve(
                seat=self.args.hot_seat,
                token=self.args.tokens[index],
                key=f"burst-hot-{uuid.uuid4()}",
            )
            for index in range(self.args.concurrency)
        ]
        results = await asyncio.gather(*requests)
        created = sum(result.status_code == 201 for result in results)
        declined = sum(result.status_code == 409 for result in results)
        passed = (
            created == 1
            and declined == len(results) - 1
            and not self.result_5xx_count(
                [TestOutcome(name, "", True, results, [])]
            )
            and all(result.status_code in (201, 409) for result in results)
        )
        outcome = TestOutcome(
            name,
            "PASS" if passed else "FAIL",
            passed,
            results,
            [
                f"Total requests : {len(results)}",
                f"201 confirmed  : {created}",
                f"409 declined   : {declined}",
            ],
        )
        self.add_outcome(outcome)
        return outcome

    async def run_idempotency(self, seat):
        name = "Idempotency concurrency"
        if seat is None:
            outcome = self.skipped(name, "No additional available seat reserved for this test.")
            self.add_outcome(outcome)
            return outcome

        key = f"burst-idempotent-{uuid.uuid4()}"
        results = await asyncio.gather(
            *[
                self.reserve(
                    seat=seat,
                    token=self.args.tokens[self.args.concurrency],
                    key=key,
                )
                for _ in range(self.args.concurrency)
            ]
        )
        successful = [
            result for result in results if result.status_code in (200, 201)
        ]
        ids = {result.reservation_id for result in successful}
        passed = (
            len(successful) == len(results)
            and len(ids) == 1
            and None not in ids
            and sum(result.status_code == 201 for result in results) >= 1
            and not any(
                result.status_code is None
                or result.status_code >= 500
                or result.status_code not in (200, 201)
                for result in results
            )
        )
        outcome = TestOutcome(
            name,
            "PASS" if passed else "FAIL",
            passed,
            results,
            [
                f"Total requests       : {len(results)}",
                f"201 responses        : {sum(r.status_code == 201 for r in results)}",
                f"200 replay responses : {sum(r.status_code == 200 for r in results)}",
                f"Reservation IDs      : {len(ids)} unique ID(s)",
            ],
        )
        self.add_outcome(outcome)
        return outcome

    async def run_idempotency_conflict(self, seats):
        name = "Idempotency conflict test"
        if len(seats) < 2:
            outcome = self.skipped(name, "Two additional available seats are required.")
            self.add_outcome(outcome)
            return outcome

        key = f"burst-conflict-{uuid.uuid4()}"
        first = await self.reserve(
            seat=seats[0],
            token=self.args.tokens[self.args.concurrency + 1],
            key=key,
        )
        second = await self.reserve(
            seat=seats[1],
            token=self.args.tokens[self.args.concurrency + 1],
            key=key,
        )
        passed = first.status_code == 201 and second.status_code == 409
        outcome = TestOutcome(
            name,
            "PASS" if passed else "FAIL",
            passed,
            [first, second],
            [
                f"First request  : {first.status_code}",
                f"Second request : {second.status_code}",
            ],
        )
        self.add_outcome(outcome)
        return outcome

    async def run_user_limit(self, seats):
        name = "Per-user limit test"
        request_count = min(max(self.args.concurrency, 5), 10)
        if len(seats) < request_count:
            outcome = self.skipped(
                name,
                f"{request_count} additional available seats are required; "
                f"only {len(seats)} are available.",
            )
            self.add_outcome(outcome)
            return outcome

        fresh_user_token = self.args.tokens[self.args.concurrency + 2]
        results = await asyncio.gather(
            *[
                self.reserve(
                    seat=seat,
                    token=fresh_user_token,
                    key=f"burst-limit-{uuid.uuid4()}",
                )
                for seat in seats[:request_count]
            ]
        )
        successful = [
            result for result in results if result.status_code in (200, 201)
        ]
        owned_seats = {
            seat
            for result in successful
            for seat in (
                result.response_data.get("seats", [])
                if result.response_data
                else []
            )
        }
        show_result = await self.fetch_show()
        all_results = [*results, show_result]
        if show_result.status_code == 200 and show_result.response_data:
            statuses = {
                item["seat"]: item["status"]
                for item in show_result.response_data.get("seats", [])
            }
            confirmed_owned = sum(
                statuses.get(seat) == "confirmed" for seat in owned_seats
            )
        else:
            confirmed_owned = len(owned_seats)

        passed = (
            confirmed_owned <= 4
            and len(owned_seats) <= 4
            and len(successful) <= 4
            and len(successful) >= 4
            and not any(
                result.status_code is None
                or result.status_code >= 500
                or result.status_code not in (201, 409)
                for result in results
            )
            and show_result.status_code == 200
        )
        outcome = TestOutcome(
            name,
            "PASS" if passed else "FAIL",
            passed,
            all_results,
            [
                f"Concurrent requests : {len(results)}",
                f"201 created         : {sum(r.status_code == 201 for r in results)}",
                f"200 replay          : {sum(r.status_code == 200 for r in results)}",
                f"409 declined        : {sum(r.status_code == 409 for r in results)}",
                f"Confirmed seats     : {confirmed_owned}",
                "Limit               : 4",
            ],
        )
        outcome.results = results
        self.add_outcome(outcome)
        if show_result.status_code is None or show_result.status_code >= 500:
            print(
                "State query failure: "
                f"status={show_result.status_code}, "
                f"request_id={show_result.request_id}, "
                f"error={show_result.error or 'none'}"
            )
        return outcome

    async def run_reconciliation(self):
        name = "Seat reconciliation"
        result = await self.fetch_show()
        counts = (
            result.response_data.get("counts", {})
            if result.response_data
            else {}
        )
        expected = {"available", "held", "confirmed", "total"}
        complete = expected.issubset(counts)
        reconciles = (
            complete
            and counts["available"] + counts["held"] + counts["confirmed"]
            == counts["total"]
        )
        passed = result.status_code == 200 and reconciles
        details = []
        if complete:
            details.extend(
                [
                    f"Available : {counts['available']}",
                    f"Held      : {counts['held']}",
                    f"Confirmed : {counts['confirmed']}",
                    f"Total     : {counts['total']}",
                ]
            )
        else:
            details.append(
                f"Could not read show counts (HTTP {result.status_code})."
            )
        outcome = TestOutcome(
            name,
            "PASS" if passed else "FAIL",
            passed,
            [result],
            details,
        )
        self.add_outcome(outcome)
        return outcome

    def print_summary(self):
        five_xx = sum(
            result.status_code is not None and result.status_code >= 500
            for result in self.all_results
        )
        failed_requests = any(
            result.status_code is None for result in self.all_results
        )
        failures = (
            any(not outcome.passed for outcome in self.outcomes)
            or bool(five_xx)
            or failed_requests
        )

        print("\n" + "=" * 50)
        print("SEAT RESERVATION BURST TEST SUMMARY")
        print("=" * 50)
        for outcome in self.outcomes:
            print(f"{outcome.name:<28}: {outcome.state}")
        print(f"{'5xx errors':<28}: {five_xx}")
        print(f"\nOverall result             : {'FAIL' if failures else 'PASS'}")
        print("=" * 50)
        return 1 if failures else 0

    async def run(self):
        preflight = await self.fetch_show()
        if preflight.status_code != 200 or not preflight.response_data:
            self.add_outcome(
                TestOutcome(
                    "Show preflight",
                    "FAIL",
                    False,
                    [preflight],
                    [f"Could not fetch show (HTTP {preflight.status_code})."],
                )
            )
            return self.print_summary()

        seat_states = {
            item["seat"]: item["status"]
            for item in preflight.response_data.get("seats", [])
        }
        available = {
            seat for seat, state in seat_states.items() if state == "available"
        }
        remaining = sorted(available - {self.args.hot_seat})

        await self.run_hot_seat(available)

        idempotency_seat = remaining.pop(0) if remaining else None
        await self.run_idempotency(idempotency_seat)

        conflict_seats = remaining[:2]
        del remaining[: len(conflict_seats)]
        await self.run_idempotency_conflict(conflict_seats)

        limit_count = min(max(self.args.concurrency, 5), 10)
        limit_seats = remaining[:limit_count]
        del remaining[: len(limit_seats)]
        await self.run_user_limit(limit_seats)
        await self.run_reconciliation()

        return self.print_summary()


async def main():
    args = parse_args()
    runner = BurstRunner(args)
    try:
        return await runner.run()
    finally:
        await runner.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
