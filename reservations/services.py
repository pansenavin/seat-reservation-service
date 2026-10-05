import hashlib
import json
import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Count
from django.utils import timezone
from .metrics import RESERVATIONS_CANCELLED, RESERVATIONS_CONFIRMED, increment_declined
from .models import Reservation, ReservationSeat, Seat, Show

logger = logging.getLogger("seat_reservation")


class ReservationError(Exception):
    pass


class ShowNotFound(ReservationError):
    pass


class SeatsNotFound(ReservationError):
    def __init__(self, seat_numbers):
        self.seat_numbers = seat_numbers


class SeatsUnavailable(ReservationError):
    def __init__(self, seat_numbers):
        self.seat_numbers = seat_numbers


class UserSeatLimitExceeded(ReservationError):
    pass


class IdempotencyKeyReused(ReservationError):
    pass


class ReservationAmountTooLarge(ReservationError):
    pass


class ReservationNotFound(ReservationError):
    pass


class ReservationNotOwned(ReservationError):
    pass


def _request_hash(seat_numbers):
    canonical_body = json.dumps(
        {"seats": sorted(seat_numbers)},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_body.encode("utf-8")).hexdigest()


def _lock_user_show(user_id, show_id):
    if connection.vendor != "postgresql":
        raise RuntimeError(
            "Reservation concurrency requires the configured PostgreSQL database."
        )

    lock_digest = hashlib.blake2b(
        f"{show_id}:{user_id}".encode("ascii"),
        digest_size=8,
    ).digest()
    lock_key = int.from_bytes(lock_digest, byteorder="big", signed=True)

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_key])


def _record_idempotent_replay(*, show_id, reservation_id, seat_count):
    increment_declined("idempotent_replay")
    logger.info(
        "reservation_idempotent_replay",
        extra={
            "event": "reservation_idempotent_replay",
            "show_id": show_id,
            "reservation_id": reservation_id,
            "seat_count": seat_count,
        },
    )


@transaction.atomic
def _reserve_seats_transaction(*, show_id, user_id, seat_numbers, idempotency_key):
    normalized_seats = sorted(seat_numbers)
    request_hash = _request_hash(normalized_seats)

    show = Show.objects.filter(pk=show_id).first()
    if show is None:
        raise ShowNotFound

    existing = Reservation.objects.filter(
        user_id=user_id,
        show=show,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise IdempotencyKeyReused
        transaction.on_commit(
            lambda: _record_idempotent_replay(
                show_id=show_id,
                reservation_id=existing.pk,
                seat_count=len(normalized_seats),
            )
        )
        return existing, False

    # Serialize reservations for one user/show so simultaneous requests cannot
    # both pass the confirmed-seat limit check. PostgreSQL releases this lock
    # automatically when the surrounding transaction commits or rolls back.
    _lock_user_show(user_id, show_id)

    existing = Reservation.objects.filter(
        user_id=user_id,
        show=show,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise IdempotencyKeyReused
        transaction.on_commit(
            lambda: _record_idempotent_replay(
                show_id=show_id,
                reservation_id=existing.pk,
                seat_count=len(normalized_seats),
            )
        )
        return existing, False

    locked_seats = list(
        Seat.objects.select_for_update()
        .filter(show=show, seat_number__in=normalized_seats)
        .order_by("seat_number", "pk")
    )
    locked_by_number = {seat.seat_number: seat for seat in locked_seats}
    missing_seats = [
        seat_number
        for seat_number in normalized_seats
        if seat_number not in locked_by_number
    ]
    if missing_seats:
        raise SeatsNotFound(missing_seats)

    unavailable_seats = [
        seat.seat_number
        for seat in locked_seats
        if seat.status != Seat.Status.AVAILABLE
    ]
    if unavailable_seats:
        raise SeatsUnavailable(unavailable_seats)

    confirmed_seat_count = ReservationSeat.objects.filter(
        reservation__show=show,
        reservation__user_id=user_id,
        reservation__status=Reservation.Status.CONFIRMED,
    ).aggregate(total=Count("pk"))["total"]
    if (
        confirmed_seat_count + len(locked_seats)
        > settings.MAX_CONFIRMED_SEATS_PER_USER_PER_SHOW
    ):
        raise UserSeatLimitExceeded

    amount_paise = show.price_paise * len(locked_seats)
    amount_field = Reservation._meta.get_field("amount_paise")
    try:
        amount_field.clean(amount_paise, None)
    except ValidationError as error:
        raise ReservationAmountTooLarge from error

    reservation = Reservation.objects.create(
        show=show,
        user_id=user_id,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        amount_paise=amount_paise,
    )
    ReservationSeat.objects.bulk_create(
        [
            ReservationSeat(reservation=reservation, seat=seat)
            for seat in locked_seats
        ]
    )
    Seat.objects.filter(pk__in=[seat.pk for seat in locked_seats]).update(
        status=Seat.Status.CONFIRMED
    )
    transaction.on_commit(lambda: RESERVATIONS_CONFIRMED.inc())
    transaction.on_commit(
        lambda: logger.info(
            "reservation_confirmed",
            extra={
                "event": "reservation_confirmed",
                "show_id": show.pk,
                "reservation_id": reservation.pk,
                "seat_count": len(locked_seats),
            },
        )
    )

    return reservation, True


def reserve_seats(*, show_id, user_id, seat_numbers, idempotency_key):
    try:
        return _reserve_seats_transaction(
            show_id=show_id,
            user_id=user_id,
            seat_numbers=seat_numbers,
            idempotency_key=idempotency_key,
        )
    except SeatsUnavailable:
        increment_declined("seat_taken")
        logger.info(
            "reservation_declined",
            extra={
                "event": "reservation_declined",
                "reason": "seat_taken",
                "show_id": show_id,
            },
        )
        raise
    except SeatsNotFound:
        logger.info(
            "reservation_declined",
            extra={
                "event": "reservation_declined",
                "reason": "seat_not_found",
                "show_id": show_id,
            },
        )
        raise
    except UserSeatLimitExceeded:
        increment_declined("per_user_limit")
        logger.info(
            "reservation_declined",
            extra={
                "event": "reservation_declined",
                "reason": "per_user_limit",
                "show_id": show_id,
            },
        )
        raise
    except IdempotencyKeyReused:
        logger.info(
            "reservation_declined",
            extra={
                "event": "reservation_declined",
                "reason": "idempotency_conflict",
                "show_id": show_id,
            },
        )
        raise


@transaction.atomic
def cancel_reservation(*, reservation_id, user_id):
    reservation = (
        Reservation.objects.select_for_update()
        .filter(pk=reservation_id)
        .first()
    )
    if reservation is None:
        raise ReservationNotFound
    if reservation.user_id != user_id:
        raise ReservationNotOwned

    # Use the same user/show lock as reservation creation so cancellation is
    # serialized with confirmed-seat limit checks for this user and show.
    _lock_user_show(reservation.user_id, reservation.show_id)

    reservation_seats = list(
        ReservationSeat.objects.filter(reservation=reservation)
        .values_list("seat_id", "seat__seat_number")
        .order_by("seat__seat_number", "seat_id")
    )
    seat_ids = [seat_id for seat_id, _ in reservation_seats]
    seat_numbers = [seat_number for _, seat_number in reservation_seats]

    if reservation.status == Reservation.Status.CANCELLED:
        return reservation, seat_numbers, False

    locked_seats = list(
        Seat.objects.select_for_update()
        .filter(pk__in=seat_ids)
        .order_by("seat_number", "pk")
    )
    if {seat.pk for seat in locked_seats} != set(seat_ids):
        raise RuntimeError(
            "A reservation references a seat that no longer exists."
        )

    confirmed_seat_ids = [
        seat.pk
        for seat in locked_seats
        if seat.status == Seat.Status.CONFIRMED
    ]
    if len(confirmed_seat_ids) != len(locked_seats):
        raise RuntimeError(
            "A confirmed reservation references a seat that is not confirmed."
        )

    Seat.objects.filter(
        pk__in=confirmed_seat_ids,
        reservation_seats__reservation=reservation,
    ).update(status=Seat.Status.AVAILABLE)
    reservation.status = Reservation.Status.CANCELLED
    reservation.cancelled_at = timezone.now()
    reservation.save(update_fields=("status", "cancelled_at"))
    transaction.on_commit(lambda: RESERVATIONS_CANCELLED.inc())
    transaction.on_commit(
        lambda: logger.info(
            "reservation_cancelled",
            extra={
                "event": "reservation_cancelled",
                "show_id": reservation.show_id,
                "reservation_id": reservation.pk,
                "seat_count": len(seat_numbers),
            },
        )
    )

    return reservation, seat_numbers, True
