from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Seat, Show
from .services import (
    IdempotencyKeyReused,
    ReservationNotFound,
    ReservationNotOwned,
    ReservationAmountTooLarge,
    SeatsNotFound,
    SeatsUnavailable,
    ShowNotFound,
    UserSeatLimitExceeded,
    cancel_reservation,
    reserve_seats,
)
from .serializers import (
    ReservationCreateSerializer,
    ReservationResponseSerializer,
    ShowCreateSerializer,
    ShowCreatedSerializer,
    ShowDetailSerializer,
)


def _parse_user_id(value):
    if value is None or not value.isascii() or not value.isdecimal():
        return None
    user_id = int(value)
    if user_id <= 0 or user_id > 9_223_372_036_854_775_807:
        return None
    return user_id


class ShowCreateView(APIView):
    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        serializer = ShowCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            show = Show.objects.create(
                name=serializer.validated_data["name"],
                price_paise=serializer.validated_data["price_paise"],
            )
            Seat.objects.bulk_create(
                [
                    Seat(show=show, seat_number=seat_number)
                    for seat_number in serializer.validated_data["seats"]
                ]
            )

        return Response(
            ShowCreatedSerializer(
                {"id": show.pk, **serializer.validated_data}
            ).data,
            status=status.HTTP_201_CREATED,
        )


class ShowDetailView(APIView):
    authentication_classes = ()
    permission_classes = ()

    def get(self, request, show_id):
        show = Show.objects.annotate(
            total_seats=Count("seats"),
            available_seats=Count(
                "seats",
                filter=Q(seats__status=Seat.Status.AVAILABLE),
            ),
            held_seats=Count(
                "seats",
                filter=Q(seats__status=Seat.Status.HELD),
            ),
            confirmed_seats=Count(
                "seats",
                filter=Q(seats__status=Seat.Status.CONFIRMED),
            ),
        ).prefetch_related("seats").filter(pk=show_id).first()
        if show is None:
            return Response(
                {"detail": "Not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ShowDetailSerializer(show).data)


class ReservationCreateView(APIView):
    authentication_classes = ()
    permission_classes = ()

    def post(self, request, show_id):
        user_id = _parse_user_id(request.headers.get("X-User-ID"))
        if user_id is None:
            return Response(
                {"detail": "A positive integer X-User-ID header is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        idempotency_key = request.headers.get("Idempotency-Key", "")
        if not idempotency_key.strip() or len(idempotency_key) > 255:
            return Response(
                {
                    "detail": (
                        "A non-empty Idempotency-Key header of at most "
                        "255 characters is required."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ReservationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        seat_numbers = serializer.validated_data["seats"]
        try:
            reservation, created = reserve_seats(
                show_id=show_id,
                user_id=user_id,
                seat_numbers=seat_numbers,
                idempotency_key=idempotency_key,
            )
        except ShowNotFound:
            return Response(
                {"error": "show_not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except SeatsNotFound as error:
            return Response(
                {"error": "seat_not_found", "seats": error.seat_numbers},
                status=status.HTTP_409_CONFLICT,
            )
        except SeatsUnavailable as error:
            return Response(
                {"error": "seat_taken", "seats": error.seat_numbers},
                status=status.HTTP_409_CONFLICT,
            )
        except UserSeatLimitExceeded:
            return Response(
                {"error": "per_user_limit"},
                status=status.HTTP_409_CONFLICT,
            )
        except IdempotencyKeyReused:
            return Response(
                {"error": "idempotency_key_reused_with_different_request"},
                status=status.HTTP_409_CONFLICT,
            )
        except ReservationAmountTooLarge:
            return Response(
                {"error": "reservation_amount_too_large"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            ReservationResponseSerializer(reservation).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class ReservationCancelView(APIView):
    authentication_classes = ()
    permission_classes = ()

    def post(self, request, reservation_id):
        user_id = _parse_user_id(request.headers.get("X-User-ID"))
        if user_id is None:
            return Response(
                {"detail": "A positive integer X-User-ID header is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            reservation, seat_numbers, _ = cancel_reservation(
                reservation_id=reservation_id,
                user_id=user_id,
            )
        except ReservationNotFound:
            return Response(
                {"error": "reservation_not_found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ReservationNotOwned:
            return Response(
                {"error": "not_allowed"},
                status=status.HTTP_403_FORBIDDEN,
            )

        return Response(
            {
                "reservation_id": reservation.pk,
                "status": reservation.status,
                "seats": seat_numbers,
            },
            status=status.HTTP_200_OK,
        )
