import asyncio
import random
import re
import unicodedata
from time import monotonic
from urllib.parse import urlsplit

import httpx

from models.meme import Meme
from services.database import Database

IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


class SearchError(RuntimeError):
    pass


def https_host(url: str) -> str | None:
    if len(url) > 2000 or any(char.isspace() or ord(char) < 32 for char in url) or "\\" in url:
        return None
    try:
        parts = urlsplit(url)
        if (parts.scheme == "https" and parts.netloc == parts.hostname
                and parts.path.startswith("/") and not parts.fragment):
            return parts.hostname
    except ValueError:
        pass
    return None


async def image_available(client: httpx.AsyncClient, url: str, hosts: set[str] | frozenset[str]) -> bool:
    if https_host(url) not in hosts:
        return False
    try:
        response = await client.head(url, follow_redirects=False)
        if response.status_code in {403, 405, 501}:
            # 部份圖床/CDN（如 S3/儲存端）不支援 HEAD 預檢，退回使用 GET 串流預檢
            async with client.stream("GET", url, follow_redirects=False) as get_resp:
                content_type = get_resp.headers.get("content-type", "").split(";")[0].lower()
                return get_resp.is_success and content_type in IMAGE_TYPES
        content_type = response.headers.get("content-type", "").split(";")[0].lower()
        return response.is_success and content_type in IMAGE_TYPES
    except httpx.HTTPError:
        return False


class CommonsSearch:
    def __init__(self, client: httpx.AsyncClient, contact: str):
        self.client = client
        self.contact = contact
        self.next_request = 0.0
        self.last_request = 0.0
        self.lock = asyncio.Lock()

    async def search(self, tags: list[str]) -> Meme | None:
        if not self.contact:
            return None
        for tag in tags:
            async with self.lock:
                if monotonic() < self.next_request:
                    raise SearchError("網路梗圖搜尋正在冷卻，請稍後再試。")
                await asyncio.sleep(max(0.0, self.last_request + 1 - monotonic()))
                self.last_request = monotonic()
                try:
                    response = await self.client.get(
                        "https://commons.wikimedia.org/w/api.php",
                        headers={"User-Agent": f"MemeAIDiscord/1.0 ({self.contact})"},
                        params={
                            "action": "query", "generator": "search",
                            "gsrsearch": tag.replace("_", " "), "gsrnamespace": 6,
                            "gsrlimit": 5, "prop": "imageinfo",
                            "iiprop": "url|mime|extmetadata",
                            "iiextmetadatafilter": "LicenseShortName|Restrictions|AttributionRequired",
                            "format": "json", "formatversion": 2,
                        },
                    )
                    if response.status_code in (429, 503):
                        retry = response.headers.get("Retry-After", "")
                        seconds = max(60, int(retry)) if retry.isascii() and retry.isdigit() and len(retry) < 10 else 60
                        self.next_request = monotonic() + seconds
                    response.raise_for_status()
                    payload = response.json()
                    if "error" in payload:
                        raise SearchError("網路梗圖 API 暫時無法使用。")
                    pages = sorted(payload.get("query", {}).get("pages", []),
                                   key=lambda page: page.get("index", 0))
                    for page in pages:
                        for info in page.get("imageinfo", []):
                            meme = self.result(info, tag)
                            if meme:
                                return meme
                except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
                    raise SearchError("網路梗圖 API 暫時無法使用。") from exc
        return None

    @staticmethod
    def result(info: dict, tag: str) -> Meme | None:
        metadata = info.get("extmetadata", {})
        license_name = metadata.get("LicenseShortName", {}).get("value")
        image_url = info.get("url", "")
        source = info.get("descriptionurl", "")
        if (license_name in {"CC0", "CC0 1.0", "Public domain"}
                and not metadata.get("Restrictions", {}).get("value")
                and metadata.get("AttributionRequired", {}).get("value") != "true"
                and info.get("mime") in IMAGE_TYPES
                and https_host(image_url) == "upload.wikimedia.org"
                and https_host(source) == "commons.wikimedia.org"):
            return Meme(image_url, (tag,), f"Wikimedia Commons · {license_name}", source)
        return None


def _description_terms(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    terms = set(re.findall(r"[a-z0-9]+", normalized))
    for phrase in re.findall(r"[\u3400-\u9fff]+", normalized):
        for size in (2, 3):
            terms.update(phrase[index:index + size] for index in range(len(phrase) - size + 1))
    return terms


class MemeSearch:
    def __init__(self, database: Database, web: CommonsSearch, image_hosts: set[str]):
        self.database = database
        self.web = web
        self.image_hosts = image_hosts

    async def search(
        self, guild_id: int, tags: list[str], *, context: str = "", fallback_tags: list[str] | None = None,
    ) -> Meme | None:
        local = await asyncio.to_thread(self.database.search, guild_id, tags) if tags else []
        if context and len(local) > 1:
            query_tags = set(tags)
            best_score = len(query_tags.intersection(local[0].tags))
            best = []
            for meme in local:
                if len(query_tags.intersection(meme.tags)) != best_score:
                    break
                best.append(meme)

            if len(best) > 1:
                terms = _description_terms(context)
                random.shuffle(best)
                best.sort(key=lambda meme: sum(
                    len(term) for term in terms.intersection(_description_terms(meme.description))
                ), reverse=True)
                local = best + local[len(best):]
        for meme in local[:5]:
            if await image_available(self.web.client, meme.image_url, self.image_hosts):
                return meme
        search_tags = fallback_tags or tags
        return await self.web.search(search_tags) if search_tags else None


