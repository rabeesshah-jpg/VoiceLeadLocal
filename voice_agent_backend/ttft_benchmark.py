"""
TTFT benchmark: OpenRouter vs direct OpenAI, gpt-4o-mini, streaming.

Usage:
    pip install openai
    export OPENAI_API_KEY=sk-...
    export OPENROUTER_API_KEY=sk-or-...
    python ttft_benchmark.py

Runs N trials against each endpoint with a realistic short prompt
(similar length to your call turns) and reports individual + average TTFT.
"""

import os
import time
import statistics
from openai import OpenAI

N_TRIALS = 8

# Use a prompt similar to your real conversation turns
TEST_MESSAGES = [
    {"role": "system", "content": "You are Noura, a website intake assistant. Keep replies to one short sentence."},
    {"role": "user", "content": "I'm based in Lahore and I run a jewelry store."},
]

def measure_ttft(client: OpenAI, model: str, extra_body: dict | None = None) -> float:
    start = time.perf_counter()
    stream = client.chat.completions.create(
        model=model,
        messages=TEST_MESSAGES,
        stream=True,
        max_tokens=50,
        extra_body=extra_body or {},
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            return time.perf_counter() - start
    return time.perf_counter() - start  # fallback if no content chunk found


def run_trials(label: str, client: OpenAI, model: str, extra_body: dict | None = None):
    print(f"\n=== {label} ===")
    times = []
    for i in range(N_TRIALS):
        t = measure_ttft(client, model, extra_body)
        times.append(t)
        print(f"  trial {i+1}: {t:.3f}s")
    print(f"  avg: {statistics.mean(times):.3f}s   min: {min(times):.3f}s   max: {max(times):.3f}s")
    return times


if __name__ == "__main__":
    openai_key = os.environ.get("OPENAI_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")

    if openai_key:
        direct_client = OpenAI(api_key=openai_key)
        run_trials("Direct OpenAI - gpt-4o-mini", direct_client, "gpt-4o-mini")
    else:
        print("Skipping direct OpenAI test - OPENAI_API_KEY not set")

    if openrouter_key:
        or_client = OpenAI(api_key=openrouter_key, base_url="https://openrouter.ai/api/v1")
        # Pin provider to OpenAI only - remove this extra_body to see OpenRouter's default routing
        run_trials(
            "OpenRouter (pinned to OpenAI provider) - gpt-4o-mini",
            or_client,
            "openai/gpt-4o-mini",
            extra_body={"provider": {"order": ["openai"], "allow_fallbacks": False}},
        )
        run_trials(
            "OpenRouter (default routing, no pin) - gpt-4o-mini",
            or_client,
            "openai/gpt-4o-mini",
        )
    else:
        print("Skipping OpenRouter test - OPENROUTER_API_KEY not set")