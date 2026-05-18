from django.urls import path

from apps.calls.views import (
    CallDetailView,
    CallEventsView,
    EndCallView,
    HealthView,
    StartCallView,
)

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("calls/start/", StartCallView.as_view(), name="calls-start"),
    path("calls/<uuid:call_id>/end/", EndCallView.as_view(), name="calls-end"),
    path("calls/<uuid:call_id>/", CallDetailView.as_view(), name="calls-detail"),
    path("calls/<uuid:call_id>/events/", CallEventsView.as_view(), name="calls-events"),
]
