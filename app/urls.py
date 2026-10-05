from django.contrib import admin
from django.urls import include, path
from rest_framework.decorators import api_view
from rest_framework.response import Response

from app.health import live, ready
from reservations.metrics import metrics


@api_view(["GET"])
def health_check(request):
    return Response({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health_check, name="health"),
    path("api/health/live/", live, name="health-live"),
    path("api/health/ready/", ready, name="health-ready"),
    path("metrics", metrics, name="metrics"),
    path("api/", include("reservations.urls")),
]