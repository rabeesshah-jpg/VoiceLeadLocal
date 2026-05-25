#!/usr/bin/env python3
"""
Streaming latency test client for the Chatterbox TTS server (deployed on RunPod).

It POSTs to the server's /tts endpoint with "stream": true, consumes the audio
response as an HTTP stream, and reports:

  * time to response headers   (connect + TLS + request + server accepts)
  * TIME TO FIRST AUDIO CHUNK  <-- the headline metric
  * generation gap            (first chunk minus headers = server "think" time)
  * transfer duration, total wall-clock time
  * bytes received, chunk count, throughput
  * decoded audio duration and real-time factor

The received audio is saved to disk. If the server streams raw PCM (no header),
the script wraps it in a WAV container so the file is still playable.

Usage
-----
  python3 test_tts_stream.py
  python3 test_tts_stream.py --text "Some other sentence to synthesize."
  python3 test_tts_stream.py --url https://<pod-id>-8000.proxy.runpod.net
  python3 test_tts_stream.py --no-stream             # request the whole file at once
  python3 test_tts_stream.py --runs 5                # repeat (reuses connection)
  python3 test_tts_stream.py --chunk-size 80         # smaller chunks -> earlier audio
  python3 test_tts_stream.py --no-split              # disable server-side chunking

Requires: requests   ->   pip install requests
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
    sys.exit("This script needs the 'requests' package.\n"
             "Install it with:  pip install requests")

# The live RunPod deployment. Override with --url for a different pod.
DEFAULT_URL = "https://hzzc2ch2wg5sjm-8000.proxy.runpod.net"

DEFAULT_TEXT = (
    "Hello! This is a streaming latency test for the Chatterbox "
    "text to speech server running on a RunPod RTX 4090."
)

# The Chatterbox engine renders mono 16-bit PCM at this rate. Used as a
# fallback when the server streams headerless raw PCM, and to derive duration
# (a streamed WAV's header carries a bogus data size, so it can't be trusted).
PCM_SAMPLE_RATE = 24000
PCM_CHANNELS = 1
PCM_SAMPLE_WIDTH = 2  # bytes (16-bit)
WAV_HEADER_BYTES = 44


def human_bytes(n):
    """Format a byte count as a short human-readable string."""
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024


def detect_format(data):
    """Return a short label for the audio container in `data`."""
    if data[:4] == b"RIFF":
        return "wav"
    if data[:4] == b"OggS":
        return "ogg/opus"
    if data[:3] == b"ID3" or data[:2] == b"\xff\xfb":
        return "mp3"
    return "raw-pcm"


def wrap_pcm_as_wav(pcm_bytes, sample_rate):
    """Wrap headerless 16-bit mono PCM in a WAV container so it is playable."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(PCM_CHANNELS)
        w.setsampwidth(PCM_SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


def audio_duration_seconds(data, sample_rate):
    """Best-effort decoded duration in seconds. Returns None if unknown.

    A WAV streamed over chunked HTTP usually carries a placeholder data-chunk
    size in its header, so wave.getnframes() is garbage. We therefore read only
    the (reliable) channel/width/rate fields and derive the frame count from the
    number of bytes actually received.
    """
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
    return None  # mp3 / opus: needs a decoder to measure, skip


def run_once(args, run_index, session):
    """Perform a single streaming request and return its metrics dict."""
    endpoint = args.url.rstrip("/") + "/tts"
    payload = {
        "text": args.text,
        "voice_mode": "predefined",
        "predefined_voice_id": args.voice,
        "output_format": args.format,
        "split_text": not args.no_split,
        "chunk_size": args.chunk_size,
        "stream": not args.no_stream,
        "seed": args.seed,
    }

    label = f"run {run_index}" if args.runs > 1 else "request"
    print(f"\n=== {label}: POST {endpoint} "
          f"(stream={payload['stream']}, split={payload['split_text']}, "
          f"chunk_size={payload['chunk_size']}) ===")

    start = time.perf_counter()
    try:
        resp = session.post(endpoint, json=payload, stream=True,
                            timeout=args.timeout)
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

    # chunk_size=None yields data to us as soon as it arrives off the socket,
    # which is what makes the "first chunk" timing meaningful.
    for chunk in resp.iter_content(chunk_size=None):
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
          f"   (connect + TLS + server accept)")
    print(f"  Time to FIRST audio chunk: {ttfc_ms:9.1f} ms"
          f"   ({human_bytes(first_chunk_size)} in chunk 1)")
    print(f"  -> generation gap        : {gen_gap_ms:9.1f} ms"
          f"   (server work before any audio)")
    print(f"  Transfer duration        : {transfer_ms:9.1f} ms")
    print(f"  Total wall-clock time    : {total_ms:9.1f} ms")
    print(f"  Chunks / bytes received  : {chunk_count} chunks, "
          f"{human_bytes(total_bytes)} ({total_bytes} bytes)")
    if total_ms > 0:
        print(f"  Average throughput       : "
              f"{human_bytes(total_bytes / (total_ms / 1000))}/s")
    if duration:
        rtf = (total_ms / 1000) / duration
        print(f"  Decoded audio duration   : {duration:9.2f} s")
        print(f"  Real-time factor         : {rtf:9.2f}x "
              f"({'faster' if rtf < 1 else 'slower'} than real time)")

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
        description="Streaming latency test client for the Chatterbox TTS API.")
    parser.add_argument("--url", default=DEFAULT_URL,
                        help="Base URL of the TTS server")
    parser.add_argument("--text", default=DEFAULT_TEXT,
                        help="Text to synthesize")
    parser.add_argument("--voice", default="Emily.wav",
                        help="Predefined voice id (see /get_predefined_voices)")
    parser.add_argument("--format", default="wav",
                        help="output_format field (wav, opus, ...)")
    parser.add_argument("--seed", type=int, default=0,
                        help="Generation seed (0 = random)")
    parser.add_argument("--chunk-size", type=int, default=240,
                        help="Server-side text chunk size; smaller can lower "
                             "time-to-first-chunk on longer text")
    parser.add_argument("--no-split", action="store_true",
                        help="Disable server-side text chunking (split_text=false)")
    parser.add_argument("--out", default="tts_stream_output.wav",
                        help="File to save the received audio to")
    parser.add_argument("--sample-rate", type=int, default=PCM_SAMPLE_RATE,
                        help="Sample rate used to decode/estimate duration")
    parser.add_argument("--timeout", type=float, default=300,
                        help="Per-request timeout in seconds")
    parser.add_argument("--runs", type=int, default=1,
                        help="Number of requests to send (connection is reused; "
                             "results averaged at the end)")
    parser.add_argument("--no-stream", action="store_true",
                        help="Send stream=false to compare non-streaming latency")
    args = parser.parse_args()

    print(f"Target : {args.url}")
    print(f"Text   : {len(args.text)} chars - {args.text[:72]}"
          f"{'...' if len(args.text) > 72 else ''}")

    # A single Session keeps the TCP/TLS connection alive across runs, so only
    # run 1 pays the handshake cost -- closer to how a real client behaves.
    session = requests.Session()
    try:
        results = [run_once(args, i + 1, session) for i in range(args.runs)]
    finally:
        session.close()

    # Save the audio from the last run, wrapping raw PCM if needed.
    last = results[-1]
    data, container = last["data"], last["container"]
    if container == "raw-pcm":
        data = wrap_pcm_as_wav(data, args.sample_rate)
        print(f"\nNote: server streamed headerless raw PCM; wrapped it in a "
              f"WAV header ({args.sample_rate} Hz, mono, 16-bit).")
    with open(args.out, "wb") as fh:
        fh.write(data)

    print("\n" + "=" * 60)
    if args.runs > 1:
        ttfcs = [r["ttfc_ms"] for r in results]
        warm = ttfcs[1:] if len(ttfcs) > 1 else ttfcs
        print(f"Runs                      : {args.runs}")
        print(f"Time to first chunk (run1): {ttfcs[0]:9.1f} ms  (cold connection)")
        print(f"Time to first chunk (avg) : {statistics.mean(ttfcs):9.1f} ms")
        print(f"Time to first chunk (warm): {statistics.mean(warm):9.1f} ms  "
              f"(runs 2+, connection reused)")
        print(f"Time to first chunk (min) : {min(ttfcs):9.1f} ms")
        print(f"Time to first chunk (max) : {max(ttfcs):9.1f} ms")
    else:
        print(f"TIME TO FIRST AUDIO CHUNK : {results[0]['ttfc_ms']:.1f} ms")
    print(f"Audio saved to            : {args.out}")
    print("=" * 60)


if __name__ == "__main__":
    main()
