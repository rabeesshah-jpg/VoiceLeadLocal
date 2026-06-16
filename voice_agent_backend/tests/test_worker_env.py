import os

from agent.worker_env import (
    cuda_visible_devices,
    database_url_configured,
    deepgram_remote_client,
    enforce_worker_runtime_policy,
    openrouter_remote_client,
    resolve_openrouter_model,
    resolve_tts_base_url,
    resolve_tts_health_url,
    tts_health_check_label,
    worker_cpu_only,
    worker_env,
)


def test_worker_env_default(monkeypatch):
    monkeypatch.delenv("WORKER_ENV", raising=False)
    assert worker_env() == "local"


def test_runpod_tts_defaults(monkeypatch):
    monkeypatch.setenv("WORKER_ENV", "runpod")
    monkeypatch.delenv("TTS_BASE_URL", raising=False)
    monkeypatch.delenv("CHATTERBOX_TTS_URL", raising=False)
    assert resolve_tts_base_url() == "http://127.0.0.1:7788"
    assert resolve_tts_health_url() == "http://127.0.0.1:7788/v1/health"


def test_explicit_tts_overrides_runpod_default(monkeypatch):
    monkeypatch.setenv("WORKER_ENV", "runpod")
    monkeypatch.setenv("TTS_BASE_URL", "http://10.0.0.5:7788")
    assert resolve_tts_base_url() == "http://10.0.0.5:7788"


def test_tts_health_url_override(monkeypatch):
    monkeypatch.setenv("TTS_HEALTH_URL", "http://127.0.0.1:7788/v1/health")
    assert resolve_tts_health_url() == "http://127.0.0.1:7788/v1/health"


def test_openrouter_model_alias(monkeypatch):
    monkeypatch.delenv("VOICE_AGENT_LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-3-haiku")
    assert resolve_openrouter_model() == "anthropic/claude-3-haiku"


def test_database_url_configured(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url_configured() is False
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    assert database_url_configured() is True


def test_worker_cpu_only_on_runpod(monkeypatch):
    monkeypatch.setenv("WORKER_ENV", "runpod")
    monkeypatch.delenv("WORKER_CPU_ONLY", raising=False)
    assert worker_cpu_only() is True


def test_enforce_worker_runtime_policy_hides_cuda_on_runpod(monkeypatch):
    monkeypatch.setenv("WORKER_ENV", "runpod")
    monkeypatch.setenv("STT_PROVIDER", "faster_whisper")
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    enforce_worker_runtime_policy()
    assert os.environ["CUDA_VISIBLE_DEVICES"] == ""
    assert os.environ["STT_PROVIDER"] == "deepgram"


def test_enforce_worker_runtime_policy_skips_local_dev(monkeypatch):
    monkeypatch.setenv("WORKER_ENV", "local")
    monkeypatch.delenv("WORKER_CPU_ONLY", raising=False)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setenv("STT_PROVIDER", "faster_whisper")
    enforce_worker_runtime_policy()
    assert "CUDA_VISIBLE_DEVICES" not in os.environ
    assert os.environ["STT_PROVIDER"] == "faster_whisper"


def test_remote_client_flags(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "deepgram")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert deepgram_remote_client() is True
    assert openrouter_remote_client() is True


def test_tts_health_check_label():
    assert tts_health_check_label(True) == "pass"
    assert tts_health_check_label(False) == "fail"
    assert tts_health_check_label(None) == "unknown"


def test_cuda_visible_devices_empty_means_no_gpu(monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    assert cuda_visible_devices() == ""
