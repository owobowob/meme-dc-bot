import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from models.meme import Meme
from services.meme_search import CommonsSearch, MemeSearch, SearchError, image_available


def test_empty():
    database = Mock()
    database.search.return_value = []
    web = Mock(search=AsyncMock(return_value=None))
    assert asyncio.run(MemeSearch(database, web, set()).search(1, ["doge", "stonks"])) is None
    web.search.assert_awaited_once_with(["doge", "stonks"])


def test_local_first():
    meme = Meme("https://i.imgur.com/a.gif", ("doge",), "", "https://i.imgur.com/a.gif")
    database = Mock()
    database.search.return_value = [meme]
    web = Mock(search=AsyncMock())
    web.client.head = AsyncMock(return_value=httpx.Response(200, headers={"Content-Type": "image/gif"}))
    assert asyncio.run(MemeSearch(database, web, {"i.imgur.com"}).search(1, ["doge"])) == meme
    web.search.assert_not_awaited()


def test_api_rate_limit():
    async def run():
        request = Mock(return_value=httpx.Response(429, headers={"Retry-After": "60"}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(request)) as client:
            search = CommonsSearch(client, "example@example.org")
            with pytest.raises(SearchError, match="暫時"):
                await search.search(["doge"])
            with pytest.raises(SearchError, match="冷卻"):
                await search.search(["doge"])
            request.assert_called_once()
    asyncio.run(run())


@pytest.mark.parametrize("url", ["https://127.0.0.1/a.png", "https://i.imgur.com@localhost/a", "http://i.imgur.com/a"])
def test_untrusted_image_is_not_requested(url):
    client = Mock(head=AsyncMock())
    assert not asyncio.run(image_available(client, url, {"i.imgur.com"}))
    client.head.assert_not_awaited()


@pytest.mark.parametrize("status_code", [403, 405, 501])
def test_image_available_head_fallback_to_get_stream(status_code):
    url = "https://storageapi.miyago9267.com/f/test.jpg"
    hosts = {"storageapi.miyago9267.com"}

    def handler(request: httpx.Request):
        if request.method == "HEAD":
            return httpx.Response(status_code)
        elif request.method == "GET":
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"image_bytes")
        return httpx.Response(400)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await image_available(client, url, hosts)
            assert result is True

    asyncio.run(run())
