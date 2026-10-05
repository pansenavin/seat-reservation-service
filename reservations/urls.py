from django.urls import path
from .views import ReservationCancelView, ReservationCreateView, ShowCreateView, ShowDetailView

urlpatterns = [
    path("shows", ShowCreateView.as_view(), name="show-create"),
    path("shows/<int:show_id>", ShowDetailView.as_view(), name="show-detail"),
    path("shows/<int:show_id>/reserve",ReservationCreateView.as_view(),name="show-reserve"),
    path("reservations/<int:reservation_id>/cancel",ReservationCancelView.as_view(),name="reservation-cancel"),
]
