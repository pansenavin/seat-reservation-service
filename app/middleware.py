import logging
import time
import uuid
from django.utils.deprecation import MiddlewareMixin
from .observability import request_id_context

logger = logging.getLogger("seat_reservation")


class RequestIDMiddleware(MiddlewareMixin):
    """Attach and log the correlation ID for each request."""

    def process_request(self, request):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.request_id = request_id
        request._request_id_context_token = request_id_context.set(request_id)
        request._request_started_at = time.monotonic()

        logger.info(
            "request_started",
            extra={
                "event": "request_started",
                "request_id": request_id,
                "method": request.method,
                "path": request.path,
            },
        )

    def process_exception(self, request, exception):
        logger.error(
            "request_exception",
            extra={
                "event": "request_exception",
                "request_id": getattr(request, "request_id", None),
                "method": request.method,
                "path": request.path,
                "exception_type": type(exception).__name__,
            },
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        return None

    def process_response(self, request, response):
        request_id = getattr(request, "request_id", None)
        if request_id is not None:
            response["X-Request-ID"] = request_id
            duration_ms = round(
                (time.monotonic() - request._request_started_at) * 1000,
                3,
            )
            logger.info(
                "request_completed",
                extra={
                    "event": "request_completed",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            request_id_context.reset(request._request_id_context_token)
        return response
