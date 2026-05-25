"""DEPRECATED — use apps.calls.services.supertonic_warmup."""

from apps.calls.services.supertonic_warmup import (  # noqa: F401
    run_supertonic_handshake,
    run_supertonic_handshake_on_api_startup,
)

run_chatterbox_handshake = run_supertonic_handshake
run_chatterbox_handshake_on_api_startup = run_supertonic_handshake_on_api_startup
