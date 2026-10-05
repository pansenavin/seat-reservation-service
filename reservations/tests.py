from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase
from rest_framework.test import APIClient

from .models import Seat, Show


class ShowAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.create_url = "/api/shows/"

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
                "id": 1,
                "name": "Avengers 7 PM",
                "price_paise": 25000,
                "seats": ["A1", "A2", "A3"],
            },
        )
        self.assertEqual(Show.objects.count(), 1)
        self.assertEqual(Seat.objects.count(), 3)

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

        response = self.client.get(f"{self.create_url}{show.pk}/")

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

        response = self.client.get(f"{self.create_url}{show.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["counts"],
            {"total": 5, "available": 5, "held": 0, "confirmed": 0},
        )
        self.assertEqual(len(response.json()["seats"]), 5)

    def test_get_nonexistent_show_returns_json_404(self):
        response = self.client.get(f"{self.create_url}999999/")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Not found."})
