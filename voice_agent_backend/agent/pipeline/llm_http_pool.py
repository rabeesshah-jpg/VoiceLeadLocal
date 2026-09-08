from __future__ import annotations

import httpx
from openai import AsyncOpenAI

_shared_clients: dict[tuple[str, str | None], AsyncOpenAI] = {}


def get_shared_openai_client(api_key: str, base_url: str | None = None) -> AsyncOpenAI:
    key = (api_key, base_url)
    client = _shared_clients.get(key)
    if client is None:
        transport = httpx.AsyncHTTPTransport(retries=2)
        http_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=15.0,  # well under Cloudflare/Groq's likely idle timeout
            ),
            timeout=httpx.Timeout(10.0, connect=10.0),
            transport=transport,
        )
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
            max_retries=2,  # let the OpenAI SDK's own retry layer catch a dead-connection failure too
        )
        _shared_clients[key] = client
    return client