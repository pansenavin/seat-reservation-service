from rest_framework import serializers

from .models import Reservation, Seat, Show


class StrictCharField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class ShowCreateSerializer(serializers.Serializer):
    name = StrictCharField(max_length=255, allow_blank=False)
    seats = serializers.ListField(
        child=StrictCharField(max_length=50, allow_blank=False),
        allow_empty=False,
    )
    price_paise = serializers.IntegerField(min_value=1, max_value=2_147_483_647)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            price_paise = data.get("price_paise")
            if isinstance(price_paise, bool) or not isinstance(price_paise, int):
                raise serializers.ValidationError(
                    {"price_paise": "A positive integer paise value is required."}
                )
        return super().to_internal_value(data)

    def validate_seats(self, seats):
        if len(seats) != len(set(seats)):
            raise serializers.ValidationError(
                "Seat numbers must be unique within a show."
            )
        return seats


class ShowCreatedSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    price_paise = serializers.IntegerField()
    seats = serializers.ListField(child=serializers.CharField())


class SeatStatusSerializer(serializers.ModelSerializer):
    seat = serializers.CharField(source="seat_number")

    class Meta:
        model = Seat
        fields = ("seat", "status")


class ShowCountsSerializer(serializers.Serializer):
    total = serializers.IntegerField(source="total_seats")
    available = serializers.IntegerField(source="available_seats")
    held = serializers.IntegerField(source="held_seats")
    confirmed = serializers.IntegerField(source="confirmed_seats")


class ShowDetailSerializer(serializers.ModelSerializer):
    counts = ShowCountsSerializer(source="*")
    seats = SeatStatusSerializer(many=True)

    class Meta:
        model = Show
        fields = ("id", "name", "price_paise", "counts", "seats")


class ReservationCreateSerializer(serializers.Serializer):
    seats = serializers.ListField(
        child=StrictCharField(max_length=50, allow_blank=False),
        allow_empty=False,
    )

    def to_internal_value(self, data):
        if isinstance(data, dict) and "user_id" in data:
            raise serializers.ValidationError(
                {"user_id": "User identity must be provided by the request header."}
            )
        return super().to_internal_value(data)

    def validate_seats(self, seats):
        if len(seats) != len(set(seats)):
            raise serializers.ValidationError(
                "Seat numbers must be unique within a reservation."
            )
        return seats


class ReservationResponseSerializer(serializers.ModelSerializer):
    reservation_id = serializers.IntegerField(source="pk", read_only=True)
    show_id = serializers.IntegerField(read_only=True)
    seats = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = (
            "reservation_id",
            "show_id",
            "user_id",
            "seats",
            "amount_paise",
            "status",
        )

    def get_seats(self, reservation):
        return list(
            reservation.reservation_seats.select_related("seat")
            .order_by("seat__seat_number")
            .values_list("seat__seat_number", flat=True)
        )
