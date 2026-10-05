# Generated manually because Django commands are unavailable in this environment.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Show",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("price_paise", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ("name",),
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(price_paise__gte=0),
                        name="show_price_paise_nonnegative",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Seat",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("seat_number", models.CharField(max_length=50)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("available", "Available"),
                            ("held", "Held"),
                            ("confirmed", "Confirmed"),
                        ],
                        default="available",
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "show",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="seats",
                        to="reservations.show",
                    ),
                ),
            ],
            options={
                "ordering": ("show", "seat_number"),
                "indexes": [
                    models.Index(
                        fields=["show", "status"],
                        name="seat_show_status_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("show", "seat_number"),
                        name="seat_show_number_uniq",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Reservation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("user_id", models.BigIntegerField()),
                ("idempotency_key", models.CharField(max_length=255)),
                ("request_hash", models.CharField(max_length=255)),
                ("amount_paise", models.PositiveIntegerField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("confirmed", "Confirmed"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="confirmed",
                        max_length=10,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                (
                    "show",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reservations",
                        to="reservations.show",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "indexes": [
                    models.Index(
                        fields=["show", "status"],
                        name="reservation_show_status_idx",
                    ),
                    models.Index(
                        fields=["user_id", "show", "status"],
                        name="res_user_show_status_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user_id", "show", "idempotency_key"),
                        name="reservation_user_show_key_uniq",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(amount_paise__gte=0),
                        name="reservation_amount_paise_nonnegative",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="ReservationSeat",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "reservation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reservation_seats",
                        to="reservations.reservation",
                    ),
                ),
                (
                    "seat",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reservation_seats",
                        to="reservations.seat",
                    ),
                ),
            ],
            options={
                "ordering": ("reservation", "seat"),
                "constraints": [
                    models.UniqueConstraint(
                        fields=("reservation", "seat"),
                        name="reservation_seat_pair_uniq",
                    ),
                ],
            },
        ),
    ]
