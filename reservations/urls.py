from django.urls import path

from .views import ShowCreateView, ShowDetailView


urlpatterns = [
    path("shows/", ShowCreateView.as_view(), name="show-create"),
    path("shows/<int:show_id>/", ShowDetailView.as_view(), name="show-detail"),
]
