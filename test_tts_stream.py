#!/usr/bin/env python3
"""
Latency test for Supertonic-3 TTS (POST /v1/tts).

Matches voice-agent env:
  TTS_BASE_URL=http://172.16.2.158:7788
  TTS_MODEL=supertonic-3
  TTS_VOICE=M1
  TTS_LANG=en

Usage
-----
  python3 test_tts_stream.py
  python3 test_tts_stream.py --voice F1 --lang en
  python3 test_tts_stream.py --text "Your sentence."
  python3 test_tts_stream.py --runs 3 --chunk-size 4096

Requires: requests  ->  pip install requests
"""

import argparse
import io
import statistics
import struct
import sys
import time
import wave

try:
    import requests
except ImportError:
    sys.exit("Install requests:  pip install requests")

DEFAULT_URL = "http://172.16.2.158:7788"
DEFAULT_MODEL = "supertonic-3"
DEFAULT_VOICE = "M1"
DEFAULT_LANG = "en"
DEFAULT_TEXT = (
    "Hello! This is a streaming latency test for the multilingual "
    "text to speech server running on a RunPod RTX 4090."
)
DEFAULT_OUT = "output.wav"
DEFAULT_READ_CHUNK_SIZE = 4096

# Supertonic serve returns 44.1 kHz mono WAV
PCM_SAMPLE_RATE = 44100
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2
WAV_HEADER_BYTES = 44


def human_bytes(n):
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def detect_format(data):
    if data[:4] == b"RIFF":
        return "wav"
    if data[:4] == b"OggS":
        return "ogg/opus"
    if data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
        return "mp3"
    return "raw-pcm"


def wrap_pcm_as_wav(pcm_bytes, sample_rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(PCM_CHANNELS)
        w.setsampwidth(PCM_SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


def audio_duration_seconds(data, sample_rate):
    fmt = detect_format(data)
    if fmt == "wav":
        channels, width, rate = PCM_CHANNELS, PCM_SAMPLE_WIDTH, sample_rate
        try:
            with wave.open(io.BytesIO(data), "rb") as w:
                channels = w.getnchannels() or channels
                width = w.getsampwidth() or width
                rate = w.getframerate() or rate
        except (wave.Error, EOFError, struct.error):
            pass
        pcm_bytes = max(len(data) - WAV_HEADER_BYTES, 0)
        frames = pcm_bytes / (channels * width)
        return frames / rate if rate else None
    if fmt == "raw-pcm" and data:
        return len(data) / (sample_rate * PCM_CHANNELS * PCM_SAMPLE_WIDTH)
    return None


def tts_endpoint(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    for suffix in ("/v1/tts", "/tts", "/tts_to_audio"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base.rstrip("/") + "/v1/tts"


def build_payload(args) -> dict:
    payload = {
        "text": args.text,
        "voice": args.voice,
        "lang": args.lang,
        "response_format": "wav",
    }
    if args.max_chunk_length:
        payload["max_chunk_length"] = args.max_chunk_length
    return payload


def run_once(args, run_index, session):
    endpoint = tts_endpoint(args.url)
    payload = build_payload(args)

    label = f"run {run_index}" if args.runs > 1 else "request"
    print(
        f"\n=== {label}: POST {endpoint} ===\n"
        f"  model={args.model} voice={payload['voice']} lang={payload['lang']} "
        f"read_chunk={args.chunk_size}"
    )

    start = time.perf_counter()
    try:
        resp = session.post(
            endpoint,
            json=payload,
            headers={"Content-Type": "application/json"},
            stream=True,
            timeout=args.timeout,
        )
    except requests.exceptions.RequestException as exc:
        sys.exit(f"Request failed: {exc}")

    headers_at = time.perf_counter()
    if resp.status_code != 200:
        body = resp.text[:300]
        resp.close()
        sys.exit(f"Server returned HTTP {resp.status_code}: {body}")

    first_chunk_at = None
    first_chunk_size = 0
    total_bytes = 0
    chunk_count = 0
    sink = io.BytesIO()

    for chunk in resp.iter_content(chunk_size=args.chunk_size):
        if not chunk:
            continue
        if first_chunk_at is None:
            first_chunk_at = time.perf_counter()
            first_chunk_size = len(chunk)
        chunk_count += 1
        total_bytes += len(chunk)
        sink.write(chunk)
    end = time.perf_counter()
    resp.close()

    if first_chunk_at is None:
        sys.exit("No audio bytes were received from the server.")

    data = sink.getvalue()
    container = detect_format(data)
    content_type = resp.headers.get("Content-Type", "unknown")

    ttfb_ms = (headers_at - start) * 1000
    ttfc_ms = (first_chunk_at - start) * 1000
    gen_gap_ms = (first_chunk_at - headers_at) * 1000
    transfer_ms = (end - first_chunk_at) * 1000
    total_ms = (end - start) * 1000
    duration = audio_duration_seconds(data, args.sample_rate)

    print(f"  HTTP 200  Content-Type: {content_type}  ({container})")
    print(f"  Time to response headers : {ttfb_ms:9.1f} ms"
          f"   (connect + server TTS; body often starts right after)")
    print(f"  Time to FIRST audio byte : {ttfc_ms:9.1f} ms"
          f"   ({human_bytes(first_chunk_size)} in read 1)")
    print(f"  -> after headers to byte  : {gen_gap_ms:9.1f} ms")
    print(f"  Transfer duration        : {transfer_ms:9.1f} ms")
    print(f"  Total wall-clock time    : {total_ms:9.1f} ms")
    print(
        f"  Chunks / bytes received  : {chunk_count} chunks, "
        f"{human_bytes(total_bytes)} ({total_bytes} bytes)"
    )
    if total_ms > 0:
        print(
            f"  Average throughput       : "
            f"{human_bytes(total_bytes / (total_ms / 1000))}/s"
        )
    if duration:
        rtf = (total_ms / 1000) / duration
        print(f"  Decoded audio duration   : {duration:9.2f} s")
        print(
            f"  Real-time factor         : {rtf:9.2f}x "
            f"({'faster' if rtf < 1 else 'slower'} than real time)"
        )

    return {
        "ttfc_ms": ttfc_ms,
        "ttfb_ms": ttfb_ms,
        "gen_gap_ms": gen_gap_ms,
        "total_ms": total_ms,
        "bytes": total_bytes,
        "data": data,
        "container": container,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Supertonic-3 TTS latency test (POST /v1/tts)."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="TTS_BASE_URL")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="TTS_MODEL (logged)")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="TTS_VOICE (M1–M5, F1–F5)")
    parser.add_argument("--lang", default=DEFAULT_LANG, help="TTS_LANG (e.g. en, ar)")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="text field")
    parser.add_argument(
        "--max-chunk-length",
        type=int,
        default=0,
        help="optional max_chunk_length (0 = omit)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_READ_CHUNK_SIZE,
        help="HTTP read chunk size in bytes",
    )
    parser.add_argument("--out", default=DEFAULT_OUT, help="output WAV path")
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=PCM_SAMPLE_RATE,
        help="sample rate for duration estimate",
    )
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    print(f"Target : {tts_endpoint(args.url)}")
    print(f"Model  : {args.model}")
    print(f"Voice  : {args.voice}  Lang: {args.lang}")
    print(f"Text   : {len(args.text)} chars - {args.text[:72]}"
          f"{'...' if len(args.text) > 72 else ''}")

    session = requests.Session()
    session.headers.update({"Connection": "keep-alive"})
    try:
        results = [run_once(args, i + 1, session) for i in range(args.runs)]
    finally:
        session.close()

    last = results[-1]
    data, container = last["data"], last["container"]
    if container == "raw-pcm":
        data = wrap_pcm_as_wav(data, args.sample_rate)
        print(f"\nNote: wrapped PCM as WAV ({args.sample_rate} Hz).")
    with open(args.out, "wb") as fh:
        fh.write(data)

    print("\n" + "=" * 60)
    if args.runs > 1:
        ttfcs = [r["ttfc_ms"] for r in results]
        warm = ttfcs[1:] if len(ttfcs) > 1 else ttfcs
        print(f"Runs                      : {args.runs}")
        print(f"Time to first audio (run1): {ttfcs[0]:9.1f} ms  (cold connection)")
        print(f"Time to first audio (avg) : {statistics.mean(ttfcs):9.1f} ms")
        if warm:
            print(
                f"Time to first audio (warm): {statistics.mean(warm):9.1f} ms  "
                f"(runs 2+, connection reused)"
            )
        print(f"Time to first audio (min) : {min(ttfcs):9.1f} ms")
        print(f"Time to first audio (max) : {max(ttfcs):9.1f} ms")
    else:
        print(f"TIME TO FIRST AUDIO BYTE  : {results[0]['ttfc_ms']:.1f} ms")
    print(f"Audio saved to            : {args.out}")
    print("=" * 60)


if __name__ == "__main__":
    main()
