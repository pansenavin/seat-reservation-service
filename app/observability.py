import contextvars
import json
import logging
import traceback
from datetime import datetime, timezone


request_id_context = contextvars.ContextVar("request_id", default=None)

_LOG_FIELDS = (
    "event",
    "request_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "exception_type",
    "stack_trace",
    "reason",
    "show_id",
    "reservation_id",
    "seat_count",
)


class JSONFormatter(logging.Formatter):
    """Format application log records as single-line JSON."""

    def format(self, record):
        event = getattr(record, "event", None) or record.getMessage()
        entry = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": event,
        }

        request_id = getattr(record, "request_id", None)
        if request_id is None:
            request_id = request_id_context.get()
        if request_id is not None:
            entry["request_id"] = request_id

        for field in _LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None and field not in entry:
                entry[field] = value

        if record.exc_info:
            entry["exception_type"] = record.exc_info[0].__name__
            entry["stack_trace"] = "".join(
                traceback.format_tb(record.exc_info[2])
            )

        return json.dumps(entry, ensure_ascii=True, separators=(",", ":"))
