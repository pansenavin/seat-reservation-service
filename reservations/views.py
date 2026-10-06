from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema

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
    ErrorResponseSerializer,
    ReservationCancelResponseSerializer,
    ReservationCreateSerializer,
    ReservationResponseSerializer,
    ShowCreateSerializer,
    ShowCreatedSerializer,
    ShowDetailSerializer,
)


class ShowCreateView(APIView):
    authentication_classes = (SessionAuthentication, TokenAuthentication)
    permission_classes = (IsAdminUser,)

    @extend_schema(
        request=ShowCreateSerializer,
        responses={
            201: ShowCreatedSerializer,
            400: OpenApiResponse(description="Input validation error."),
        },
        tags=["Shows"],
    )
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
    permission_classes = (AllowAny,)

    @extend_schema(
        responses={
            200: ShowDetailSerializer,
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="Show not found.",
            ),
        },
        tags=["Shows"],
    )
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
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        request=ReservationCreateSerializer,
        parameters=[
            OpenApiParameter(
                name="Idempotency-Key",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.HEADER,
                required=True,
                description="Unique key for this user's reservation request.",
            ),
        ],
        responses={
            200: ReservationResponseSerializer,
            201: ReservationResponseSerializer,
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="Invalid request or reservation amount.",
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="Show not found.",
            ),
            409: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="Seat conflict, per-user limit, or idempotency conflict.",
            ),
        },
        tags=["Reservations"],
    )
    def post(self, request, show_id):
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
                user_id=request.user.pk,
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
    authentication_classes = (TokenAuthentication,)
    permission_classes = (IsAuthenticated,)

    @extend_schema(
        request=None,
        responses={
            200: ReservationCancelResponseSerializer,
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="Reservation belongs to another user.",
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="Reservation not found.",
            ),
        },
        tags=["Reservations"],
    )
    def post(self, request, reservation_id):
        try:
            reservation, seat_numbers, _ = cancel_reservation(
                reservation_id=reservation_id,
                user_id=request.user.pk,
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
