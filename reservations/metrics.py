from django.db import DatabaseError
from django.http import HttpResponse
from django.views.decorators.http import require_GET
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

from .models import Seat


RESERVATIONS_CONFIRMED = Counter(
    "reservations_confirmed_total",
    "Total number of newly confirmed reservations",
)
RESERVATIONS_CANCELLED = Counter(
    "reservations_cancelled_total",
    "Total number of reservations cancelled",
)
RESERVATIONS_DECLINED = Counter(
    "reservations_declined_total",
    "Total number of reservation attempts declined or replayed",
    labelnames=("reason",),
)
SEATS_AVAILABLE = Gauge(
    "seats_available",
    "Current number of seats available in PostgreSQL",
)

_ALLOWED_DECLINE_REASONS = {
    "seat_taken",
    "per_user_limit",
    "idempotent_replay",
}


def increment_declined(reason):
    if reason not in _ALLOWED_DECLINE_REASONS:
        raise ValueError(f"Unsupported reservation decline metric reason: {reason}")
    RESERVATIONS_DECLINED.labels(reason=reason).inc()


@require_GET
def metrics(request):
    try:
        available_seats = Seat.objects.filter(
            status=Seat.Status.AVAILABLE
        ).count()
    except DatabaseError:
        return HttpResponse(
            "PostgreSQL is unavailable; metrics could not be collected.\n",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    SEATS_AVAILABLE.set(available_seats)
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
