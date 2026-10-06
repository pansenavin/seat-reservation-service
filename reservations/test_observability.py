import json
import logging
import uuid
from contextlib import contextmanager

from django.test import TransactionTestCase

from app.observability import JSONFormatter
from reservations.models import Seat, Show
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
