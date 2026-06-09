#!/usr/bin/env python3
"""Smoke test RunPod Supertonic TTS (POST /v1/tts)."""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from pathlib import Path

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BACKEND_ROOT / "outputs"


def _load_dotenv() -> None:
    env_path = BACKEND_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _wav_duration(data: bytes) -> float:
    if len(data) < 44 or data[:4] != b"RIFF":
        return 0.0
    sample_rate = struct.unpack_from("<I", data, 24)[0]
    channels = struct.unpack_from("<H", data, 22)[0]
    bits = struct.unpack_from("<H", data, 34)[0]
    data_bytes = struct.unpack_from("<I", data, 40)[0]
    denom = sample_rate * channels * max(bits // 8, 1)
    return data_bytes / denom if denom else 0.0


def _run_test(
    *,
    base_url: str,
    payload: dict,
    out_path: Path,
    label: str,
) -> int:
    url = f"{base_url.rstrip('/')}/v1/tts"
    print(f"\n=== {label} ===")
    print(f"POST {url}")
    print(f"payload: {json.dumps(payload)}")

    timeout = float(os.environ.get("TTS_TIMEOUT", "60"))
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)

    print(f"HTTP {resp.status_code}")
    print(f"content-type: {resp.headers.get('content-type', '-')}")
    for hdr in (
        "X-TTS-Voice-Id",
        "x-tts-voice-id",
        "X-TTS-Voice-Source",
        "x-tts-voice-source",
        "X-TTS-Gen-Ms",
        "x-tts-gen-ms",
        "X-TTS-Server-Ms",
        "x-tts-server-ms",
        "X-Sample-Rate",
        "x-sample-rate",
    ):
        if hdr in resp.headers:
            print(f"{hdr}: {resp.headers[hdr]}")

    data = resp.content
    if resp.status_code != 200:
        print(f"FAIL: expected HTTP 200, body={resp.text[:500]}")
        return 1

    content_type = resp.headers.get("content-type", "")
    if "audio/wav" not in content_type and not data.startswith(b"RIFF"):
        print(f"FAIL: expected audio/wav, got {content_type!r} body={data[:200]!r}")
        return 1

    if len(data) <= 0:
        print("FAIL: empty response body")
        return 1

    duration = _wav_duration(data)
    if duration <= 0:
        print("FAIL: WAV duration is 0")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    print(f"saved: {out_path} ({len(data)} bytes, {duration:.3f}s)")
    print("PASS")
    return 0


def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="RunPod TTS smoke test")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("TTS_BASE_URL", "").strip(),
        help="RunPod TTS base URL (default: TTS_BASE_URL from .env)",
    )
    parser.add_argument(
        "--voice-id",
        default="",
        help="Optional custom voice_id for /v1/tts",
    )
    parser.add_argument(
        "--text",
        default="Hello, this is a local RunPod TTS smoke test.",
        help="Text to synthesize",
    )
    args = parser.parse_args()

    if not args.base_url:
        print("ERROR: set TTS_BASE_URL in .env or pass --base-url", file=sys.stderr)
        return 2

    lang = os.environ.get("TTS_LANG", "en")
    rc = _run_test(
        base_url=args.base_url,
        payload={
            "text": args.text,
            "voice": "M1",
            "lang": lang,
            "response_format": "wav",
        },
        out_path=OUTPUT_DIR / "local_preset_tts_test.wav",
        label="preset M1",
    )

    if args.voice_id:
        rc = rc or _run_test(
            base_url=args.base_url,
            payload={
                "text": args.text,
                "voice_id": args.voice_id,
                "lang": lang,
                "response_format": "wav",
            },
            out_path=OUTPUT_DIR / "local_custom_tts_test.wav",
            label=f"custom voice_id={args.voice_id}",
        )

    return rc


if __name__ == "__main__":
    raise SystemExit(main())
