from django.urls import include, path

from app.health import live, ready
from reservations.metrics import metrics


urlpatterns = [
    path("api/health/live/", live, name="health-live"),
    path("api/health/ready/", ready, name="health-ready"),
    path("metrics", metrics, name="metrics"),
    path("api/", include("reservations.urls")),
]