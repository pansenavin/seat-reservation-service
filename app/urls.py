from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from app.health import live, ready
from reservations.metrics import metrics


urlpatterns = [
    path("api/health/live/", live, name="health-live"),
    path("api/health/ready/", ready, name="health-ready"),
    path("metrics", metrics, name="metrics"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("", include("reservations.urls")),
]