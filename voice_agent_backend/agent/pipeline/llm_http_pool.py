from __future__ import annotations

import httpx
from openai import AsyncOpenAI

_shared_clients: dict[tuple[str, str | None], AsyncOpenAI] = {}


def get_shared_openai_client(api_key: str, base_url: str | None = None) -> AsyncOpenAI:
    key = (api_key, base_url)
    client = _shared_clients.get(key)
    if client is None:
        transport = httpx.AsyncHTTPTransport(retries=1)  # retry once on a dead pooled connection
        http_client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_keepalive_connections=10,
                max_connections=20,
                keepalive_expiry=55.0,  # bounded — avoid reusing connections OpenAI/Groq may have already closed server-side
            ),
            timeout=httpx.Timeout(10.0, connect=5.0),
            transport=transport,
        )
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
        )
        _shared_clients[key] = client
    return client