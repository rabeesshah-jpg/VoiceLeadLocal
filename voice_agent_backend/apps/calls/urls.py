from django.urls import path

from apps.calls.views import (
    CallDetailView,
    CallEventsView,
    EndCallView,
    HealthView,
    StartCallView,
    UiTelemetryView,
)
from apps.calls.voice_profile_views import (
    CloneVoiceProfileView,
    RefreshVoiceProfileView,
    SetDefaultVoiceProfileView,
    TestVoiceProfileView,
    UploadJsonVoiceProfileView,
    VoiceProfileDetailView,
    VoiceProfileListView,
)

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("ui-telemetry/", UiTelemetryView.as_view(), name="ui-telemetry"),
    path("voice-profiles/", VoiceProfileListView.as_view(), name="voice-profiles-list"),
    path(
        "voice-profiles/clone/",
        CloneVoiceProfileView.as_view(),
        name="voice-profiles-clone",
    ),
    path(
        "voice-profiles/upload-json/",
        UploadJsonVoiceProfileView.as_view(),
        name="voice-profiles-upload-json",
    ),
    path(
        "voice-profiles/<uuid:profile_id>/",
        VoiceProfileDetailView.as_view(),
        name="voice-profiles-detail",
    ),
    path(
        "voice-profiles/<uuid:profile_id>/refresh/",
        RefreshVoiceProfileView.as_view(),
        name="voice-profiles-refresh",
    ),
    path(
        "voice-profiles/<uuid:profile_id>/test/",
        TestVoiceProfileView.as_view(),
        name="voice-profiles-test",
    ),
    path(
        "voice-profiles/<uuid:profile_id>/set-default/",
        SetDefaultVoiceProfileView.as_view(),
        name="voice-profiles-set-default",
    ),
    path("calls/start/", StartCallView.as_view(), name="calls-start"),
    path("calls/<uuid:call_id>/end/", EndCallView.as_view(), name="calls-end"),
    path("calls/<uuid:call_id>/", CallDetailView.as_view(), name="calls-detail"),
    path("calls/<uuid:call_id>/events/", CallEventsView.as_view(), name="calls-events"),
]
