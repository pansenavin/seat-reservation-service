from django.db import transaction
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Seat, Show
from .serializers import (
    ShowCreateSerializer,
    ShowCreatedSerializer,
    ShowDetailSerializer,
)


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
