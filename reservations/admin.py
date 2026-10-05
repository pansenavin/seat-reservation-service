from django.contrib import admin
from .models import Reservation, ReservationSeat, Seat, Show


@admin.register(Show)
class ShowAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "price_paise", "created_at")
    search_fields = ("name",)
    list_filter = ("created_at",)


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = ("id", "show", "seat_number", "status", "created_at")
    search_fields = ("seat_number", "show__name")
    list_filter = ("show", "status", "created_at")
    list_select_related = ("show",)


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "show",
        "user_id",
        "amount_paise",
        "status",
        "created_at",
        "cancelled_at",
    )
    search_fields = ("=user_id", "idempotency_key", "show__name")
    list_filter = ("show", "status", "created_at")
    list_select_related = ("show",)


@admin.register(ReservationSeat)
class ReservationSeatAdmin(admin.ModelAdmin):
    list_display = ("id", "reservation", "seat", "created_at")
    search_fields = (
        "=reservation__user_id",
        "reservation__idempotency_key",
        "seat__seat_number",
    )
    list_filter = ("reservation__show", "created_at")
    list_select_related = ("reservation", "seat", "reservation__show")
