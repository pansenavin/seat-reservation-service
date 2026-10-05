from django.db import models
from django.db.models import Q


class Show(models.Model):
    name = models.CharField(max_length=255)
    price_paise = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.CheckConstraint(
                condition=Q(price_paise__gte=0),
                name="show_price_paise_nonnegative",
            ),
        ]

    def __str__(self):
        return self.name


class Seat(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        HELD = "held", "Held"
        CONFIRMED = "confirmed", "Confirmed"

    show = models.ForeignKey(
        Show,
        on_delete=models.CASCADE,
        related_name="seats",
    )
    seat_number = models.CharField(max_length=50)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.AVAILABLE,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("show", "seat_number")
        constraints = [
            models.UniqueConstraint(
                fields=("show", "seat_number"),
                name="seat_show_number_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=("show", "status"), name="seat_show_status_idx"),
        ]

    def __str__(self):
        return f"{self.show.name} - {self.seat_number}"


class Reservation(models.Model):
    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Confirmed"
        CANCELLED = "cancelled", "Cancelled"

    show = models.ForeignKey(
        Show,
        on_delete=models.CASCADE,
        related_name="reservations",
    )
    user_id = models.BigIntegerField()
    idempotency_key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=255)
    amount_paise = models.PositiveIntegerField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.CONFIRMED,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user_id", "show", "idempotency_key"),
                name="reservation_user_show_key_uniq",
            ),
            models.CheckConstraint(
                condition=Q(amount_paise__gte=0),
                name="reservation_amount_paise_nonnegative",
            ),
        ]
        indexes = [
            models.Index(
                fields=("show", "status"),
                name="reservation_show_status_idx",
            ),
            models.Index(
                fields=("user_id", "show", "status"),
                name="res_user_show_status_idx",
            ),
        ]

    def __str__(self):
        return f"Reservation {self.pk} ({self.status})"


class ReservationSeat(models.Model):
    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.CASCADE,
        related_name="reservation_seats",
    )
    seat = models.ForeignKey(
        Seat,
        on_delete=models.CASCADE,
        related_name="reservation_seats",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("reservation", "seat")
        constraints = [
            models.UniqueConstraint(
                fields=("reservation", "seat"),
                name="reservation_seat_pair_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.reservation} - {self.seat}"
