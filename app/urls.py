from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from app.health import LiveView
from reservations.metrics import MetricsView


urlpatterns = [
    path("api/health/live/", LiveView.as_view(), name="health-live"),
    path("metrics", MetricsView.as_view(), name="metrics"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/",SpectacularSwaggerView.as_view(url_name="schema"),name="swagger-ui"),
    path("", include("reservations.urls")),
]