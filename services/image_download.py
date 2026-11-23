import asyncio
from io import BytesIO
from typing import Collection

import httpx


async def download_image(
    client: httpx.AsyncClient, url: str, allowed_types: Collection[str],
) -> tuple[BytesIO, str] | None:
    limit = 8 * 1024 * 1024
    try:
        async with asyncio.timeout(10), client.stream("GET", url, follow_redirects=False) as response:
            mime_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if not response.is_success or mime_type not in allowed_types:
                return None
            data = BytesIO()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if data.tell() + len(chunk) > limit:
                    return None
                data.write(chunk)
            if data.tell():
                data.seek(0)
                return data, mime_type
    except (httpx.HTTPError, TimeoutError):
        return None
    return None
