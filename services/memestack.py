from time import monotonic

import httpx

from models.meme import Meme
from services.meme_search import SearchError, https_host


class MemeStackSearch:
    def __init__(self, client: httpx.AsyncClient, key: str):
        self.client = client
        self.key = key
        self.retry_at = 0.0

    async def search(self, tags: list[str]) -> Meme | None:
        for tag in tags:
            if monotonic() < self.retry_at:
                raise SearchError("MemeStack 搜尋額度暫時受限，請稍後再試。")
            try:
                response = await self.client.get(
                    "https://api.memestack.ai/v2/search",
                    headers={"X-API-Key": self.key},
                    params={"q": tag.replace("_", " "), "limit": 3},
                )
                if response.status_code == 429:
                    retry = response.headers.get("Retry-After", "")
                    seconds = int(retry) if retry.isascii() and retry.isdigit() and len(retry) < 10 else 60
                    self.retry_at = monotonic() + max(1, seconds)
                    raise SearchError("MemeStack 搜尋額度暫時受限，請稍後再試。")
                if response.status_code in (401, 403):
                    raise SearchError("MemeStack 拒絕存取，請檢查 API 金鑰。")
                response.raise_for_status()
                payload = response.json()
                if payload.get("memestack", {}).get("match_quality") in {"closest_only", "none"}:
                    continue
                for item in payload.get("results", []):
                    image = item.get("media_formats", {}).get("gif", {}).get("url", "")
                    source = item.get("itemurl", "")
                    if (https_host(image) == "api.memestack.ai"
                            and https_host(source) == "memestack.ai"):
                        return Meme(image, (tag,), "", source)
            except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
                raise SearchError("MemeStack 搜尋暫時無法使用。") from exc
        return None

