from django.apps import AppConfig


class CallsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.calls"

    def ready(self) -> None:
        from config.logging_setup import (
            setup_api_logging,
            setup_ui_telemetry_logging,
            should_reset_api_logs_on_ready,
        )

        if should_reset_api_logs_on_ready():
            from pathlib import Path

            from apps.calls.services.supertonic_warmup import run_supertonic_handshake_on_api_startup
            from apps.calls.services.provider_health import log_provider_env_status

            base_dir = Path(__file__).resolve().parent.parent.parent
            setup_api_logging(base_dir)
            setup_ui_telemetry_logging(base_dir)
            log_provider_env_status()
            from django.conf import settings

            if (
                getattr(settings, "TTS_BASE_URL", "")
                or getattr(settings, "CHATTERBOX_TTS_URL", "")
            ) and not getattr(settings, "VOICE_AGENT_SKIP_TTS_WARMUP", False):
                run_supertonic_handshake_on_api_startup()
