from django.apps import AppConfig


class CallsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.calls"

    def ready(self) -> None:
        from config.logging_setup import setup_api_logging, should_reset_api_logs_on_ready

        if should_reset_api_logs_on_ready():
            from pathlib import Path

            from apps.calls.services.supertonic_warmup import run_supertonic_handshake_on_api_startup
            from apps.calls.services.provider_health import log_provider_env_status

            base_dir = Path(__file__).resolve().parent.parent.parent
            setup_api_logging(base_dir)
            log_provider_env_status()
            run_supertonic_handshake_on_api_startup()
