import json
import logging
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from django.test import TransactionTestCase

from app.observability import JSONFormatter
from reservations.models import Reservation, Seat, Show
from reservations.tests import TestIdentityAPIClient


class JSONLogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.output = []

    def emit(self, record):
        self.output.append(self.format(record))


class RequestIDAndLoggingTests(TransactionTestCase):
    def setUp(self):
        self.client = TestIdentityAPIClient()
        self.show = Show.objects.create(name="Logging show", price_paise=100)
        Seat.objects.bulk_create(
            [
                Seat(show=self.show, seat_number=seat_number)
                for seat_number in ("A1", "A2")
            ]
        )

    @contextmanager
    def capture_json_logs(self, logger_name="seat_reservation"):
        logger = logging.getLogger(logger_name)
        handler = JSONLogCapture()
        handler.setFormatter(JSONFormatter())
        original_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            yield handler
        finally:
            logger.removeHandler(handler)
            logger.setLevel(original_level)

    def test_missing_request_id_is_generated_and_returned(self):
        response = self.client.get("/api/health/live/")

        request_id = response.headers["X-Request-ID"]
        self.assertEqual(str(uuid.UUID(request_id)), request_id)
        self.assertEqual(response.status_code, 200)

    def test_supplied_request_id_is_reused_on_success(self):
        response = self.client.get(
            "/api/health/live/",
            HTTP_X_REQUEST_ID="test-request-123",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "test-request-123")

    def test_error_responses_include_request_id(self):
        response = self.client.get(
            "/shows/999999",
            HTTP_X_REQUEST_ID="not-found-request",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["X-Request-ID"], "not-found-request")

    def test_request_completion_is_structured_json(self):
        with self.capture_json_logs() as logs:
            response = self.client.get(
                "/api/health/live/",
                HTTP_X_REQUEST_ID="structured-completion-id",
            )

        entries = [json.loads(line) for line in logs.output]
        completed = next(
            entry for entry in entries if entry["message"] == "request_completed"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(completed["request_id"], "structured-completion-id")
        self.assertEqual(completed["method"], "GET")
        self.assertEqual(completed["path"], "/api/health/live/")
        self.assertEqual(completed["status_code"], 200)
        self.assertIsInstance(completed["duration_ms"], (int, float))

    def test_unexpected_exception_logs_request_id_and_stack_without_body(self):
        self.client.raise_request_exception = False
        with patch(
            "reservations.views.reserve_seats",
            side_effect=RuntimeError("unexpected failure"),
        ), self.capture_json_logs() as logs:
            response = self.client.post(
                f"/shows/{self.show.pk}/reserve",
                {"seats": ["A1"]},
                format="json",
                HTTP_X_REQUEST_ID="exception-request-id",
                HTTP_X_USER_ID="123",
                HTTP_IDEMPOTENCY_KEY="do-not-log-this-key",
                HTTP_AUTHORIZATION="Bearer do-not-log-this-token",
            )

        entries = [json.loads(line) for line in logs.output]
        error = next(
            entry for entry in entries if entry["message"] == "request_exception"
        )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["X-Request-ID"], "exception-request-id")
        self.assertEqual(error["request_id"], "exception-request-id")
        self.assertEqual(error["exception_type"], "RuntimeError")
        self.assertTrue(error["stack_trace"])
        completed = next(
            entry for entry in entries if entry["message"] == "request_completed"
        )
        self.assertEqual(completed["status_code"], 500)
        self.assertNotIn("do-not-log-this-key", "\n".join(logs.output))
        self.assertNotIn("do-not-log-this-token", "\n".join(logs.output))

    def test_reservation_events_are_structured_and_exclude_secrets(self):
        headers = {
            "HTTP_X_REQUEST_ID": "business-event-request",
            "HTTP_X_USER_ID": "123",
            "HTTP_IDEMPOTENCY_KEY": "private-idempotency-key",
            "HTTP_AUTHORIZATION": "Bearer private-auth-token",
        }
        with self.capture_json_logs() as logs:
            created = self.client.post(
                f"/shows/{self.show.pk}/reserve",
                {"seats": ["A1"]},
                format="json",
                **headers,
            )
        self.assertEqual(created.status_code, 201)
        confirmed_entries = [json.loads(line) for line in logs.output]

        with self.capture_json_logs() as logs:
            replay = self.client.post(
                f"/shows/{self.show.pk}/reserve",
                {"seats": ["A1"]},
                format="json",
                **headers,
            )
        self.assertEqual(replay.status_code, 200)
        replay_entries = [json.loads(line) for line in logs.output]

        with self.capture_json_logs() as logs:
            conflict = self.client.post(
                f"/shows/{self.show.pk}/reserve",
                {"seats": ["A1"]},
                format="json",
                HTTP_X_REQUEST_ID="conflict-request",
                HTTP_X_USER_ID="456",
                HTTP_IDEMPOTENCY_KEY="another-private-key",
                HTTP_AUTHORIZATION="Bearer another-private-token",
            )
        self.assertEqual(conflict.status_code, 409)
        conflict_entries = [json.loads(line) for line in logs.output]

        reservation_id = created.json()["reservation_id"]
        with self.capture_json_logs() as logs:
            cancelled = self.client.post(
                f"/reservations/{reservation_id}/cancel",
                HTTP_X_REQUEST_ID="cancel-request",
                HTTP_X_USER_ID="123",
            )
        self.assertEqual(cancelled.status_code, 200)
        cancelled_entries = [json.loads(line) for line in logs.output]

        self.assertTrue(
            any(
                entry["message"] == "reservation_confirmed"
                and entry["request_id"] == "business-event-request"
                for entry in confirmed_entries
            )
        )
        self.assertTrue(
            any(
                entry["message"] == "reservation_idempotent_replay"
                and entry["request_id"] == "business-event-request"
                for entry in replay_entries
            )
        )
        self.assertTrue(
            any(
                entry["message"] == "reservation_declined"
                and entry["reason"] == "seat_taken"
                and entry["request_id"] == "conflict-request"
                for entry in conflict_entries
            )
        )
        self.assertTrue(
            any(
                entry["message"] == "reservation_cancelled"
                and entry["request_id"] == "cancel-request"
                for entry in cancelled_entries
            )
        )

        all_logs = "\n".join(
            json.dumps(entry)
            for entries in (
                confirmed_entries,
                replay_entries,
                conflict_entries,
                cancelled_entries,
            )
            for entry in entries
        )
        self.assertNotIn("private-idempotency-key", all_logs)
        self.assertNotIn("private-auth-token", all_logs)
        self.assertNotIn("another-private-key", all_logs)
        self.assertNotIn("another-private-token", all_logs)
        self.assertEqual(Reservation.objects.count(), 1)
