from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.db import IntegrityError, OperationalError, close_old_connections
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token
from prometheus_client import REGISTRY
from .models import Reservation, ReservationSeat, Seat, Show


class TestIdentityAPIClient(APIClient):
    """Authenticate test requests from fixture user IDs without HTTP headers."""

    def generic(self,method,path,data="",content_type="application/octet-stream",secure=False,**extra):
        user_id = extra.pop("HTTP_X_USER_ID", None)
        if user_id and str(user_id).isascii() and str(user_id).isdecimal():
            user = get_user_model()(pk=int(user_id))
            self.force_authenticate(user=user)
        else:
            self.force_authenticate(user=None)
        return super().generic(method,path,data=data,content_type=content_type,secure=secure,**extra)


class ShowAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=get_user_model()(pk=1, is_staff=True, is_active=True))
        self.create_url = "/shows"

    def test_homepage_links_to_swagger_documentation(self):
        response = APIClient().get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Seat Reservation")
        self.assertContains(response, 'href="/api/docs/"')

    def test_create_show_and_seats(self):
        response = self.client.post(
            self.create_url,
            {
                "name": "Avengers 7 PM",
                "seats": ["A1", "A2", "A3"],
                "price_paise": 25000,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json(),
            {
                "id": Show.objects.get().pk,
                "name": "Avengers 7 PM",
                "price_paise": 25000,
                "seats": ["A1", "A2", "A3"],
            },
        )
        self.assertEqual(Show.objects.count(), 1)
        self.assertEqual(Seat.objects.count(), 3)

    def test_show_creation_requires_admin_access(self):
        response = APIClient().post(
            "/shows",
            {
                "name": "Admin-only show",
                "seats": ["A1"],
                "price_paise": 100,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Show.objects.count(), 0)

    def test_staff_token_can_create_show_at_assignment_route(self):
        admin_user = get_user_model().objects.create_user(username="show-admin",is_staff=True)
        token = Token.objects.create(user=admin_user)

        response = APIClient().post(
            "/shows",
            {
                "name": "Friday Night",
                "seats": ["A1"],
                "price_paise": 25000,
            },
            format="json",
            HTTP_AUTHORIZATION=f"Token {token.key}",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Show.objects.get().name, "Friday Night")

    def test_invalid_create_requests_return_400(self):
        valid = {
            "name": "Avengers",
            "seats": ["A1", "A2"],
            "price_paise": 25000,
        }
        cases = (
            ({key: value for key, value in valid.items() if key != "name"}),
            ({**valid, "name": "   "}),
            ({key: value for key, value in valid.items() if key != "seats"}),
            ({**valid, "seats": []}),
            ({**valid, "seats": ["A1", "A1"]}),
            ({**valid, "seats": ["A1", ""]}),
            ({**valid, "seats": ["A1", 2]}),
            ({**valid, "price_paise": "not-a-number"}),
            ({**valid, "price_paise": 25000.0}),
            ({**valid, "price_paise": 0}),
            ({**valid, "price_paise": -1}),
        )

        for payload in cases:
            with self.subTest(payload=payload):
                response = self.client.post(
                    self.create_url,
                    payload,
                    format="json",
                )
                self.assertEqual(response.status_code, 400)

        self.assertEqual(Show.objects.count(), 0)
        self.assertEqual(Seat.objects.count(), 0)

    def test_seat_creation_failure_rolls_back_show(self):
        self.client.raise_request_exception = False
        with patch(
            "reservations.views.Seat.objects.bulk_create",
            side_effect=IntegrityError("seat insert failed"),
        ):
            response = self.client.post(
                self.create_url,
                {
                    "name": "Avengers",
                    "seats": ["A1"],
                    "price_paise": 25000,
                },
                format="json",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(Show.objects.count(), 0)
        self.assertEqual(Seat.objects.count(), 0)

    def test_get_show_includes_database_seat_status_and_counts(self):
        show = Show.objects.create(name="Avengers", price_paise=25000)
        Seat.objects.create(
            show=show,
            seat_number="A1",
            status=Seat.Status.AVAILABLE,
        )
        Seat.objects.create(
            show=show,
            seat_number="A2",
            status=Seat.Status.HELD,
        )
        Seat.objects.create(
            show=show,
            seat_number="A3",
            status=Seat.Status.CONFIRMED,
        )

        response = self.client.get(f"{self.create_url}/{show.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": show.pk,
                "name": "Avengers",
                "price_paise": 25000,
                "counts": {
                    "total": 3,
                    "available": 1,
                    "held": 1,
                    "confirmed": 1,
                },
                "seats": [
                    {"seat": "A1", "status": "available"},
                    {"seat": "A2", "status": "held"},
                    {"seat": "A3", "status": "confirmed"},
                ],
            },
        )

    def test_get_show_with_all_available_seats(self):
        show = Show.objects.create(name="Avengers", price_paise=25000)
        Seat.objects.bulk_create(
            [
                Seat(show=show, seat_number=seat_number)
                for seat_number in ("A1", "A2", "A3", "A4", "A5")
            ]
        )

        response = self.client.get(f"{self.create_url}/{show.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["counts"],
            {"total": 5, "available": 5, "held": 0, "confirmed": 0},
        )
        self.assertEqual(len(response.json()["seats"]), 5)

    def test_get_nonexistent_show_returns_json_404(self):
        response = self.client.get(f"{self.create_url}/999999")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Not found."})


class ReservationAPITests(TestCase):
    def setUp(self):
        self.client = TestIdentityAPIClient()
        self.show = Show.objects.create(name="Avengers", price_paise=25000)
        Seat.objects.bulk_create(
            [
                Seat(show=self.show, seat_number=seat_number)
                for seat_number in ("A1", "A2", "A3", "A4", "A5")
            ]
        )
        self.url = f"/shows/{self.show.pk}/reserve"

    def post_reservation(self, seats, key="request-123", user_id="123"):
        return self.client.post(
            self.url,
            {"seats": seats},
            format="json",
            HTTP_X_USER_ID=user_id,
            HTTP_IDEMPOTENCY_KEY=key,
        )

    def test_create_reservation_confirms_seats_and_calculates_amount(self):
        response = self.post_reservation(["A1", "A2"])

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json(),
            {
                "reservation_id": Reservation.objects.get().pk,
                "show_id": self.show.pk,
                "user_id": 123,
                "seats": ["A1", "A2"],
                "amount_paise": 50000,
                "status": "confirmed",
            },
        )
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(ReservationSeat.objects.count(), 2)
        self.assertEqual(
            set(
                Seat.objects.filter(show=self.show, status=Seat.Status.CONFIRMED)
                .values_list("seat_number", flat=True)
            ),
            {"A1", "A2"},
        )

    def test_identical_idempotent_retry_returns_original_reservation(self):
        first_response = self.post_reservation(["A1", "A2"])
        retry_response = self.post_reservation(["A2", "A1"])

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(retry_response.status_code, 200)
        self.assertEqual(retry_response.json(), first_response.json())
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(ReservationSeat.objects.count(), 2)

    def test_same_key_with_different_request_conflicts(self):
        self.assertEqual(self.post_reservation(["A1"]).status_code, 201)

        response = self.post_reservation(["A2"])

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(
            Seat.objects.get(show=self.show, seat_number="A2").status,
            Seat.Status.AVAILABLE,
        )

    def test_unavailable_seat_conflicts(self):
        self.assertEqual(self.post_reservation(["A1"]).status_code, 201)

        response = self.post_reservation(["A1"], key="different-key", user_id="456")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"error": "seat_taken", "seats": ["A1"]},
        )
        self.assertEqual(Reservation.objects.count(), 1)

    def test_all_or_nothing_when_one_requested_seat_is_taken(self):
        self.assertEqual(
            self.post_reservation(["A3"], key="first", user_id="456").status_code,
            201,
        )

        response = self.post_reservation(
            ["A1", "A2", "A3"],
            key="second",
            user_id="123",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "seat_taken", "seats": ["A3"]})
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(
            list(
                Seat.objects.filter(
                    show=self.show,
                    seat_number__in=("A1", "A2"),
                )
                .order_by("seat_number")
                .values_list("status", flat=True)
            ),
            [Seat.Status.AVAILABLE, Seat.Status.AVAILABLE],
        )

    def test_per_user_confirmed_seat_limit_is_four(self):
        self.assertEqual(
            self.post_reservation(["A1", "A2", "A3"]).status_code,
            201,
        )
        self.assertEqual(
            self.post_reservation(["A4"], key="fourth").status_code,
            201,
        )

        response = self.post_reservation(["A5"], key="fifth")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "per_user_limit"})
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(
            Seat.objects.get(show=self.show, seat_number="A5").status,
            Seat.Status.AVAILABLE,
        )

    @override_settings(MAX_CONFIRMED_SEATS_PER_USER_PER_SHOW=1)
    def test_per_user_seat_limit_uses_configured_setting(self):
        first = self.post_reservation(["A1"])
        second = self.post_reservation(["A2"], key="configured-limit")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json(), {"error": "per_user_limit"})

    def test_amount_above_model_field_maximum_is_rejected(self):
        self.show.price_paise = 1_100_000_000
        self.show.save(update_fields=("price_paise",))

        response = self.post_reservation(["A1", "A2"])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "reservation_amount_too_large"})
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(
            Seat.objects.filter(
                show=self.show,
                status=Seat.Status.AVAILABLE,
            ).count(),
            5,
        )

    def test_missing_or_invalid_identity_and_idempotency_key_are_rejected(self):
        missing_user = self.client.post(
            self.url,
            {"seats": ["A1"]},
            format="json",
            HTTP_IDEMPOTENCY_KEY="request-123",
        )
        missing_key = self.client.post(
            self.url,
            {"seats": ["A1"]},
            format="json",
            HTTP_X_USER_ID="123",
        )
        invalid_user = self.post_reservation(["A1"], user_id="not-an-integer")
        body_user_id = self.client.post(
            self.url,
            {"seats": ["A1"], "user_id": 999},
            format="json",
            HTTP_X_USER_ID="123",
            HTTP_IDEMPOTENCY_KEY="body-user-id",
        )

        self.assertEqual(missing_user.status_code, 401)
        self.assertEqual(missing_key.status_code, 400)
        self.assertEqual(invalid_user.status_code, 401)
        self.assertEqual(body_user_id.status_code, 400)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_token_identity_is_used_and_body_identity_is_rejected(self):
        user = get_user_model().objects.create_user(username="token-user")
        token = Token.objects.create(user=user)
        client = APIClient()

        spoofed_header = client.post(
            f"/shows/{self.show.pk}/reserve",
            {"seats": ["A1"]},
            format="json",
            HTTP_X_USER_ID="999",
            HTTP_IDEMPOTENCY_KEY="spoofed-header",
        )
        self.assertEqual(spoofed_header.status_code, 401)
        self.assertEqual(Reservation.objects.count(), 0)

        response = client.post(
            f"/shows/{self.show.pk}/reserve",
            {"seats": ["A1"]},
            format="json",
            HTTP_AUTHORIZATION=f"Token {token.key}",
            HTTP_X_USER_ID="999",
            HTTP_IDEMPOTENCY_KEY="token-derived-user",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["user_id"], user.pk)
        self.assertEqual(Reservation.objects.get().user_id, user.pk)

        spoofed_body = client.post(
            f"/shows/{self.show.pk}/reserve",
            {"seats": ["A2"], "user_id": 999},
            format="json",
            HTTP_AUTHORIZATION=f"Token {token.key}",
            HTTP_IDEMPOTENCY_KEY="token-derived-user-body",
        )
        self.assertEqual(spoofed_body.status_code, 400)
        self.assertEqual(Reservation.objects.count(), 1)

    def test_unknown_seat_or_show_is_rejected(self):
        unknown_seat = self.post_reservation(["Z99"])
        unknown_show = self.client.post(
            "/shows/999999/reserve",
            {"seats": ["A1"]},
            format="json",
            HTTP_X_USER_ID="123",
            HTTP_IDEMPOTENCY_KEY="unknown-show",
        )

        self.assertEqual(unknown_seat.status_code, 409)
        self.assertEqual(
            unknown_seat.json(),
            {"error": "seat_not_found", "seats": ["Z99"]},
        )
        self.assertEqual(unknown_show.status_code, 404)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_duplicate_seat_numbers_are_rejected(self):
        response = self.post_reservation(["A1", "A1"])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Reservation.objects.count(), 0)

    def test_failure_creating_reservation_seats_rolls_back_all_writes(self):
        self.client.raise_request_exception = False
        with patch(
            "reservations.services.ReservationSeat.objects.bulk_create",
            side_effect=IntegrityError("link insert failed"),
        ):
            response = self.post_reservation(["A1", "A2"])

        self.assertEqual(response.status_code, 500)
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(ReservationSeat.objects.count(), 0)
        self.assertEqual(
            Seat.objects.filter(
                show=self.show,
                status=Seat.Status.AVAILABLE,
            ).count(),
            5,
        )


class ReservationConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.show = Show.objects.create(name="Concurrent show", price_paise=25000)
        self.seat = Seat.objects.create(show=self.show, seat_number="A1")

    def test_concurrent_requests_for_one_seat_create_one_reservation(self):
        start_barrier = threading.Barrier(20)

        def reserve(index):
            close_old_connections()
            try:
                if index < 20:
                    start_barrier.wait(timeout=30)
                client = TestIdentityAPIClient()
                response = client.post(
                    f"/shows/{self.show.pk}/reserve",
                    {"seats": ["A1"]},
                    format="json",
                    HTTP_X_USER_ID=str(index + 1),
                    HTTP_IDEMPOTENCY_KEY=f"concurrent-{index}",
                )
                return response.status_code
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=20) as executor:
            response_statuses = list(executor.map(reserve, range(50)))

        self.assertEqual(response_statuses.count(201), 1)
        self.assertEqual(response_statuses.count(409), 49)
        self.assertEqual(
            Seat.objects.get(pk=self.seat.pk).status,
            Seat.Status.CONFIRMED,
        )
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(ReservationSeat.objects.count(), 1)

    def test_concurrent_requests_cannot_exceed_user_seat_limit(self):
        more_seats = [
            Seat(show=self.show, seat_number=seat_number)
            for seat_number in ("A2", "A3", "A4", "A5")
        ]
        Seat.objects.bulk_create(more_seats)
        prior_reservation = Reservation.objects.create(
            show=self.show,
            user_id=77,
            idempotency_key="prior-reservation",
            request_hash="prior",
            amount_paise=75000,
        )
        prior_seats = list(
            Seat.objects.filter(show=self.show, seat_number__in=("A1", "A2", "A3"))
        )
        ReservationSeat.objects.bulk_create(
            [
                ReservationSeat(reservation=prior_reservation, seat=seat)
                for seat in prior_seats
            ]
        )
        Seat.objects.filter(pk__in=[seat.pk for seat in prior_seats]).update(
            status=Seat.Status.CONFIRMED
        )
        start_barrier = threading.Barrier(2)

        def reserve(seat_number, key):
            close_old_connections()
            try:
                start_barrier.wait(timeout=30)
                client = TestIdentityAPIClient()
                response = client.post(
                    f"/shows/{self.show.pk}/reserve",
                    {"seats": [seat_number]},
                    format="json",
                    HTTP_X_USER_ID="77",
                    HTTP_IDEMPOTENCY_KEY=key,
                )
                return response
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda arguments: reserve(*arguments),
                    (("A4", "at-limit-a"), ("A5", "at-limit-b")),
                )
            )

        self.assertEqual(
            sorted(response.status_code for response in responses),
            [201, 409],
        )
        limit_conflict = next(
            response for response in responses if response.status_code == 409
        )
        self.assertEqual(limit_conflict.json(), {"error": "per_user_limit"})
        self.assertEqual(Reservation.objects.filter(user_id=77).count(), 2)
        self.assertEqual(
            ReservationSeat.objects.filter(reservation__user_id=77).count(),
            4,
        )


class ReservationCancellationAPITests(TestCase):
    def setUp(self):
        self.client = TestIdentityAPIClient()
        self.show = Show.objects.create(name="Cancellation show", price_paise=25000)
        Seat.objects.bulk_create(
            [
                Seat(show=self.show, seat_number=seat_number)
                for seat_number in ("A1", "A2", "A3")
            ]
        )
        response = self.client.post(
            f"/shows/{self.show.pk}/reserve",
            {"seats": ["A1", "A2"]},
            format="json",
            HTTP_X_USER_ID="123",
            HTTP_IDEMPOTENCY_KEY="cancel-test-reservation",
        )
        self.assertEqual(response.status_code, 201)
        self.reservation_id = response.json()["reservation_id"]
        self.url = f"/reservations/{self.reservation_id}/cancel"

    def cancel_as(self, user_id="123"):
        return self.client.post(self.url, HTTP_X_USER_ID=user_id)

    def test_cancellation_releases_only_its_seats_and_updates_show_counts(self):
        response = self.cancel_as()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "reservation_id": self.reservation_id,
                "status": "cancelled",
                "seats": ["A1", "A2"],
            },
        )
        reservation = Reservation.objects.get(pk=self.reservation_id)
        self.assertEqual(reservation.status, Reservation.Status.CANCELLED)
        self.assertIsNotNone(reservation.cancelled_at)
        self.assertEqual(
            list(
                Seat.objects.filter(show=self.show)
                .order_by("seat_number")
                .values_list("seat_number", "status")
            ),
            [
                ("A1", Seat.Status.AVAILABLE),
                ("A2", Seat.Status.AVAILABLE),
                ("A3", Seat.Status.AVAILABLE),
            ],
        )

        details = self.client.get(f"/shows/{self.show.pk}")
        self.assertEqual(details.status_code, 200)
        self.assertEqual(
            details.json()["counts"],
            {"total": 3, "available": 3, "held": 0, "confirmed": 0},
        )

    def test_another_user_cannot_cancel_reservation(self):
        response = self.cancel_as(user_id="456")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "not_allowed"})
        self.assertEqual(
            Reservation.objects.get(pk=self.reservation_id).status,
            Reservation.Status.CONFIRMED,
        )
        self.assertEqual(
            Seat.objects.filter(
                show=self.show,
                seat_number__in=("A1", "A2"),
                status=Seat.Status.CONFIRMED,
            ).count(),
            2,
        )

    def test_repeated_cancellation_is_idempotent(self):
        first = self.cancel_as()
        second = self.cancel_as()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(ReservationSeat.objects.count(), 2)

    def test_missing_reservation_returns_404(self):
        response = self.client.post(
            "/reservations/999999/cancel",
            HTTP_X_USER_ID="123",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "reservation_not_found"})

    def test_user_identity_is_required(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            Reservation.objects.get(pk=self.reservation_id).status,
            Reservation.Status.CONFIRMED,
        )


class ReservationCancellationConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.show = Show.objects.create(name="Concurrent cancellation", price_paise=25000)
        seats = [
            Seat(show=self.show, seat_number=seat_number)
            for seat_number in ("A1", "A2")
        ]
        Seat.objects.bulk_create(seats)
        self.reservation = Reservation.objects.create(
            show=self.show,
            user_id=123,
            idempotency_key="concurrent-cancel",
            request_hash="cancel-hash",
            amount_paise=50000,
        )
        ReservationSeat.objects.bulk_create(
            [
                ReservationSeat(reservation=self.reservation, seat=seat)
                for seat in seats
            ]
        )
        Seat.objects.filter(pk__in=[seat.pk for seat in seats]).update(
            status=Seat.Status.CONFIRMED
        )

    def test_concurrent_cancellations_transition_once_and_return_success(self):
        barrier = threading.Barrier(2)

        def cancel():
            close_old_connections()
            try:
                barrier.wait(timeout=30)
                client = TestIdentityAPIClient()
                return client.post(
                    f"/reservations/{self.reservation.pk}/cancel",
                    HTTP_X_USER_ID="123",
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: cancel(), range(2)))

        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(
            Reservation.objects.get(pk=self.reservation.pk).status,
            Reservation.Status.CANCELLED,
        )
        self.assertEqual(
            Seat.objects.filter(
                show=self.show,
                status=Seat.Status.AVAILABLE,
            ).count(),
            2,
        )
        self.assertTrue(all(response.json()["seats"] == ["A1", "A2"] for response in responses))

    def test_cancellation_and_new_reservation_leave_consistent_seat_state(self):
        barrier = threading.Barrier(2)

        def cancel():
            close_old_connections()
            try:
                barrier.wait(timeout=30)
                return TestIdentityAPIClient().post(
                    f"/reservations/{self.reservation.pk}/cancel",
                    HTTP_X_USER_ID="123",
                )
            finally:
                close_old_connections()

        def reserve():
            close_old_connections()
            try:
                barrier.wait(timeout=30)
                return TestIdentityAPIClient().post(
                    f"/shows/{self.show.pk}/reserve",
                    {"seats": ["A1"]},
                    format="json",
                    HTTP_X_USER_ID="456",
                    HTTP_IDEMPOTENCY_KEY="reserve-after-cancel",
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            cancel_future = executor.submit(cancel)
            reserve_future = executor.submit(reserve)
            cancel_response = cancel_future.result()
            reserve_response = reserve_future.result()

        self.assertEqual(cancel_response.status_code, 200)
        self.assertIn(reserve_response.status_code, (201, 409))
        seat = Seat.objects.get(show=self.show, seat_number="A1")
        if reserve_response.status_code == 201:
            self.assertEqual(seat.status, Seat.Status.CONFIRMED)
        else:
            self.assertEqual(seat.status, Seat.Status.AVAILABLE)


class HealthEndpointTests(TestCase):
    def test_liveness_does_not_query_database(self):
        with patch("app.health.connection.cursor") as cursor:
            response = self.client.get("/api/health/live/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        cursor.assert_not_called()

    def test_readiness_executes_select_one(self):
        cursor = MagicMock()
        cursor.__enter__.return_value = cursor
        with patch("app.health.connection.cursor", return_value=cursor):
            response = self.client.get("/api/health/ready/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        cursor.execute.assert_called_once_with("SELECT 1")
        cursor.fetchone.assert_called_once_with()

    def test_readiness_returns_503_when_database_is_unavailable(self):
        with patch(
            "app.health.connection.cursor",
            side_effect=OperationalError("database unavailable"),
        ):
            response = self.client.get("/api/health/ready/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})


class MetricsEndpointTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.client = TestIdentityAPIClient()
        self.show = Show.objects.create(name="Metrics show", price_paise=100)
        Seat.objects.bulk_create(
            [
                Seat(show=self.show, seat_number=seat_number)
                for seat_number in ("A1", "A2", "A3", "A4", "A5")
            ]
        )

    @staticmethod
    def metric_sample(name, labels=None):
        return REGISTRY.get_sample_value(name, labels or {}) or 0

    def test_metrics_use_prometheus_format_and_database_seat_count(self):
        response = self.client.get("/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"# HELP reservations_confirmed_total", response.content)
        self.assertIn(b"# TYPE reservations_confirmed_total counter", response.content)
        self.assertIn(b"# TYPE reservations_declined_total counter", response.content)
        self.assertIn(b"# TYPE reservations_cancelled_total counter", response.content)
        self.assertIn(b"# TYPE seats_available gauge", response.content)
        self.assertIn(b"seats_available 5.0", response.content)

    def test_success_replay_declines_and_cancellation_metrics(self):
        confirmed_before = self.metric_sample("reservations_confirmed_total")
        replay_before = self.metric_sample(
            "reservations_declined_total",
            {"reason": "idempotent_replay"},
        )
        taken_before = self.metric_sample(
            "reservations_declined_total",
            {"reason": "seat_taken"},
        )
        limit_before = self.metric_sample(
            "reservations_declined_total",
            {"reason": "per_user_limit"},
        )
        cancelled_before = self.metric_sample("reservations_cancelled_total")

        reservation_response = self.client.post(
            f"/shows/{self.show.pk}/reserve",
            {"seats": ["A1"]},
            format="json",
            HTTP_X_USER_ID="123",
            HTTP_IDEMPOTENCY_KEY="metrics-reservation",
        )
        self.assertEqual(reservation_response.status_code, 201)
        reservation_id = reservation_response.json()["reservation_id"]

        replay_response = self.client.post(
            f"/shows/{self.show.pk}/reserve",
            {"seats": ["A1"]},
            format="json",
            HTTP_X_USER_ID="123",
            HTTP_IDEMPOTENCY_KEY="metrics-reservation",
        )
        self.assertEqual(replay_response.status_code, 200)

        taken_response = self.client.post(
            f"/shows/{self.show.pk}/reserve",
            {"seats": ["A1"]},
            format="json",
            HTTP_X_USER_ID="456",
            HTTP_IDEMPOTENCY_KEY="metrics-seat-taken",
        )
        self.assertEqual(taken_response.status_code, 409)

        for index, seat_number in enumerate(("A2", "A3", "A4")):
            response = self.client.post(
                f"/shows/{self.show.pk}/reserve",
                {"seats": [seat_number]},
                format="json",
                HTTP_X_USER_ID="123",
                HTTP_IDEMPOTENCY_KEY=f"metrics-limit-{index}",
            )
            self.assertEqual(response.status_code, 201)

        limit_response = self.client.post(
            f"/shows/{self.show.pk}/reserve",
            {"seats": ["A5"]},
            format="json",
            HTTP_X_USER_ID="123",
            HTTP_IDEMPOTENCY_KEY="metrics-limit-exceeded",
        )
        self.assertEqual(limit_response.status_code, 409)

        cancellation_response = self.client.post(
            f"/reservations/{reservation_id}/cancel",
            HTTP_X_USER_ID="123",
        )
        self.assertEqual(cancellation_response.status_code, 200)
        repeated_cancellation = self.client.post(
            f"/reservations/{reservation_id}/cancel",
            HTTP_X_USER_ID="123",
        )
        self.assertEqual(repeated_cancellation.status_code, 200)

        self.assertEqual(
            self.metric_sample("reservations_confirmed_total") - confirmed_before,
            4,
        )
        self.assertEqual(
            self.metric_sample(
                "reservations_declined_total",
                {"reason": "idempotent_replay"},
            )
            - replay_before,
            1,
        )
        self.assertEqual(
            self.metric_sample(
                "reservations_declined_total",
                {"reason": "seat_taken"},
            )
            - taken_before,
            1,
        )
        self.assertEqual(
            self.metric_sample(
                "reservations_declined_total",
                {"reason": "per_user_limit"},
            )
            - limit_before,
            1,
        )
        self.assertEqual(
            self.metric_sample("reservations_cancelled_total") - cancelled_before,
            1,
        )

    def test_failed_reservation_transaction_does_not_increment_success_counter(self):
        confirmed_before = self.metric_sample("reservations_confirmed_total")
        self.client.raise_request_exception = False

        with patch(
            "reservations.services.ReservationSeat.objects.bulk_create",
            side_effect=IntegrityError("reservation seat insert failed"),
        ):
            response = self.client.post(
                f"/shows/{self.show.pk}/reserve",
                {"seats": ["A1"]},
                format="json",
                HTTP_X_USER_ID="123",
                HTTP_IDEMPOTENCY_KEY="metrics-rollback",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            self.metric_sample("reservations_confirmed_total"),
            confirmed_before,
        )
        self.assertEqual(Reservation.objects.count(), 0)
        self.assertEqual(
            Seat.objects.get(show=self.show, seat_number="A1").status,
            Seat.Status.AVAILABLE,
        )
