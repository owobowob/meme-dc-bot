import asyncio
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from models.meme import Meme
from scripts import import_mygo_api as importer
from services.database import GLOBAL_GUILD_ID, Database

API = "https://mygo.miyago9267.com/api/v1/images"
STORAGE = "https://storageapi.miyago9267.com/f/"


@pytest.mark.parametrize("paged", [False, True])
def test_fetch_images_and_rank(paged, monkeypatch):
    pauses = AsyncMock()
    monkeypatch.setattr(importer.asyncio, "sleep", pauses)
    items = [{"id": 1, "popularity": 30}, {"id": 2},
             {"id": 3, "popularity": 80}, {"id": 4, "popularity": 60}]
    requests = []

    def respond(request):
        page = int(request.url.params["page"])
        limit = int(request.url.params["limit"])
        requests.append((page, limit))
        batch = items[(page - 1) * 2:page * 2] if paged else items
        return httpx.Response(200, json={"data": batch, "meta": {"page": page, "hasNext": paged and page == 1}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await importer.fetch_images(client, API)

    fetched = asyncio.run(run())
    assert requests == ([(1, 2000), (2, 2)] if paged else [(1, 2000)])
    if paged:
        pauses.assert_awaited_once_with(0.3)
    else:
        pauses.assert_not_awaited()
    assert fetched == items
    assert [item["id"] for item in importer.select_images(fetched, 2, 20)] == [3, 4]
    assert [item["id"] for item in importer.select_images(fetched, 0, 30)] == [3, 4, 1]
    assert [item["id"] for item in importer.select_images(fetched, 0, 0)] == [3, 4, 1, 2]


@pytest.mark.parametrize(("item", "expected"), [
    ({"episode": "mygo_11", "tags": ["燈", "愛音", "燈"],
      "description": "大家才發現三天後就要上台", "alt": "真不敢相信"},
     "出處：mygo_11｜角色：燈、愛音｜情境：大家才發現三天後就要上台｜標題／台詞：真不敢相信"),
    ({"episode": " ", "tags": [], "description": "", "alt": " 爽世皺眉側目 "}, "標題／台詞：爽世皺眉側目"),
    ({"alt": "皺眉", "description": "皺眉", "tags": [None, " "]}, "標題／台詞：皺眉"),
    ({}, ""),
])
def test_format_description(item, expected):
    assert importer.format_description(item) == expected


def test_import_skips_duplicate_urls_before_get_and_gemini(tmp_path, monkeypatch, capsys):
    database = Database(tmp_path / "memes.db")
    database.initialize()
    existing_url = STORAGE + "existing.jpg"
    new_url = STORAGE + "new.jpg"
    broken_url = STORAGE + "broken.jpg"
    database.add(2, Meme(existing_url, ("panic",), "舊資料", existing_url))
    analyze = Mock(return_value=["panic", "mygo"])
    pauses = AsyncMock()
    monkeypatch.setattr(importer, "extract_tags_from_image", analyze)
    monkeypatch.setattr(importer.asyncio, "sleep", pauses)
    requested = []

    def respond(request):
        assert request.method == "GET"
        requested.append(str(request.url))
        if str(request.url) == broken_url:
            return httpx.Response(404)
        return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"image")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await importer.import_images(client, database, [
                {"url": existing_url, "alt": "舊圖"},
                {"url": new_url, "alt": "真不敢相信", "description": "即將上台"},
                {"url": new_url},
                {"url": broken_url},
            ], "mygo")

    assert asyncio.run(run()) == importer.ImportStats(added=1, skipped=2, failed=1)
    assert requested == [new_url, broken_url]
    description = "情境：即將上台｜標題／台詞：真不敢相信"
    analyze.assert_called_once_with(b"image", "image/jpeg", description=description)
    assert [call.args for call in pauses.await_args_list] == [(1.5,), (1.5,)]
    meme = database.search(GLOBAL_GUILD_ID, ["mygo"])[0]
    assert (meme.image_url, meme.tags, meme.description) == (new_url, ("panic", "mygo"), description)
    assert capsys.readouterr().out.count("[SKIP]") == 2


def test_dry_run_previews_without_database_or_gemini(monkeypatch, capsys):
    monkeypatch.setenv("MYGO_API_URL", API)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    args = importer.parse_args(["--dry-run", "--top", "1", "--extra-tag", "MyGO"])
    assert args.extra_tag == "mygo"
    fetch = AsyncMock(return_value=[{"popularity": 10, "alt": "冷門"}, {"popularity": 90, "alt": "熱門"}])
    monkeypatch.setattr(importer, "fetch_images", fetch)
    database = Mock()
    analyze = Mock()
    register = Mock()
    monkeypatch.setattr(importer, "Database", database)
    monkeypatch.setattr(importer, "extract_tags_from_image", analyze)
    monkeypatch.setattr(importer, "register_work_tag", register)

    assert asyncio.run(importer.run(args)) == 0
    output = capsys.readouterr().out
    assert "人氣 90｜標題／台詞：熱門" in output
    assert "冷門" not in output
    database.assert_not_called()
    analyze.assert_not_called()
    register.assert_not_called()
