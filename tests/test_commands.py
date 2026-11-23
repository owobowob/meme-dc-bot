import asyncio
from functools import partial
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import httpx
import pytest
from discord import app_commands

from cogs.admin_commands import AdminCommands
from cogs.meme_commands import MemeCommands
from models.meme import Meme
from services.analyzer import ChatSearchTerms
from services.database import Database
from services.discord_service import Recommendations
from services.meme_search import MemeSearch
from services.memestack import MemeStackSearch
from services.tag_store import load_known_tags, register_new_tags


def interaction():
    return NS(
        guild_id=1, user=NS(id=2), created_at=discord.utils.utcnow(),
        channel=None, permissions=discord.Permissions.none(), app_permissions=discord.Permissions.none(),
        response=NS(defer=AsyncMock(), is_done=Mock(return_value=True)),
        followup=NS(send=AsyncMock()),
    )


def test_meme(monkeypatch):
    analyze = Mock(return_value=ChatSearchTerms(matched_known_tags=["doge", "stonks"], fallback_search_tags=[]))
    monkeypatch.setattr("services.discord_service.analyze_chat_search", analyze)
    url = "https://i.imgur.com/a.gif"
    # 本地命中 (meme.id = 42)
    search = Mock(search=AsyncMock(return_value=Meme(url, ("doge",), "", url, id=42)))
    cog = MemeCommands(Recommendations(search))
    event = interaction()
    asyncio.run(cog.meme.callback(cog, event, "看漲"))
    event.response.defer.assert_awaited_once()
    analyze.assert_called_once_with("看漲")
    embed = event.followup.send.call_args.kwargs["embed"]
    assert embed.image.url == url
    # 驗證來源顯示透明化：本地資料庫標記
    source_field = [f for f in embed.fields if f.name == "來源"][0]
    assert "（本地資料庫 #42）" in source_field.value
    search.search.assert_awaited_once_with(1, ["doge", "stonks"], context="看漲", fallback_tags=[])


def test_recent():
    def message(text, bot=False):
        return NS(content=text, author=NS(bot=bot), webhook_id=None)

    async def history(*, limit):
        assert limit == 12
        for item in (message("結果"), message("bot", bot=True), message("原因")):
            yield item

    event = interaction()
    event.channel = Mock(spec=discord.TextChannel, history=history)
    event.permissions = event.app_permissions = discord.Permissions(read_message_history=True)
    recommendations = Mock(send=AsyncMock())
    cog = MemeCommands(recommendations)
    asyncio.run(cog.meme_recent.callback(cog, event))
    recommendations.send.assert_awaited_once_with(event, "訊息 1：原因\n訊息 2：結果")


def test_meme_reranks_tied_candidates_using_recent_dialogue(monkeypatch, tmp_path):
    analyze = Mock(return_value=ChatSearchTerms(matched_known_tags=["speechless", "confused"], fallback_search_tags=[]))
    monkeypatch.setattr("services.discord_service.analyze_chat_search", analyze)
    monkeypatch.setattr("services.meme_search.image_available", AsyncMock(return_value=True))
    database = Database(tmp_path / "memes.db")
    database.initialize()
    matching_url = "https://i.imgur.com/match.png"
    other_url = "https://i.imgur.com/other.png"
    database.add(1, Meme(matching_url, ("speechless",), "遇到離譜發言時回一句我到底看了什麼。", matching_url))
    database.add(1, Meme(other_url, ("speechless",), "看到同事又忘記帶鑰匙時只能無奈地搖頭。", other_url))
    assert database.search(1, ["speechless"])[0].image_url == other_url

    async def history(*, limit):
        yield NS(content="我到底看了什麼", author=NS(bot=False), webhook_id=None)

    event = interaction()
    event.channel = Mock(spec=discord.TextChannel, history=history)
    event.permissions = event.app_permissions = discord.Permissions(read_message_history=True)
    cog = MemeCommands(Recommendations(MemeSearch(database, Mock(), {"i.imgur.com"})))
    asyncio.run(cog.meme.callback(cog, event, "幫我回一句"))

    analyze.assert_called_once_with("幫我回一句")
    assert event.followup.send.call_args.kwargs["embed"].image.url == matching_url


@pytest.mark.parametrize("local_match", [True, False])
def test_meme_tries_recovered_known_tags_before_external_search(monkeypatch, tmp_path, local_match):
    monkeypatch.setattr("services.analyzer.DEFAULT_PROVIDER", "gemini")
    monkeypatch.setattr("services.analyzer.load_known_tags", lambda: ["despair", "giving_up"])
    monkeypatch.setattr("services.analyzer.load_work_tags", lambda: set())
    monkeypatch.setattr("services.analyzer._generate_gemini_text", Mock(return_value=NS(parsed={
        "matched_known_tags": [],
        "fallback_search_tags": ["hopeless", "despair", "giving_up"],
    })))
    monkeypatch.setattr("services.meme_search.image_available", AsyncMock(return_value=True))
    database = Database(tmp_path / "memes.db")
    database.initialize()
    tags = ("despair", "giving_up")
    best_url = "https://i.imgur.com/best.png"
    if local_match:
        database.add(1, Meme(best_url, tags, "怎麼努力都覺得沒救了", best_url))
        other_url = "https://i.imgur.com/other.png"
        database.add(1, Meme(other_url, tags, "打電動連輸三場", other_url))
    web = Mock(search=AsyncMock(return_value=None))
    cog = MemeCommands(Recommendations(MemeSearch(database, web, {"i.imgur.com"})))
    event = interaction()

    asyncio.run(cog.meme.callback(cog, event, "我覺得沒救了"))

    if local_match:
        web.search.assert_not_awaited()
        assert event.followup.send.call_args.kwargs["embed"].image.url == best_url
    else:
        web.search.assert_awaited_once_with(["hopeless"])


@pytest.mark.parametrize("has_result", [True, False])
def test_meme_routes_external_keywords_to_memestack(monkeypatch, has_result):
    analyze = Mock(return_value=ChatSearchTerms(matched_known_tags=[], fallback_search_tags=["distracted_boyfriend"]))
    monkeypatch.setattr("services.discord_service.analyze_chat_search", analyze)
    database = Mock(spec=Database)
    image_url = "https://api.memestack.ai/v1/images/test/canonical.jpg"
    source = "https://memestack.ai/gallery/test"

    def respond(request):
        if str(request.url) == image_url:
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"fake_jpeg")
        assert request.url.path == "/v2/search"
        assert request.url.params["q"] == "distracted boyfriend"
        result = {"itemurl": source, "media_formats": {"gif": {"url": image_url}}}
        return httpx.Response(200, json={"results": [result] if has_result else []})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            cog = MemeCommands(Recommendations(MemeSearch(database, MemeStackSearch(client, "test"), set())))
            event = interaction()
            await cog.meme.callback(cog, event, "回頭看別人被女友發現")
            return event.followup.send.call_args

    sent = asyncio.run(run())
    database.search.assert_not_called()
    if has_result:
        embed = sent.kwargs["embed"]
        assert embed.url == source
        assert "外部搜尋 · via MemeStack" in next(field.value for field in embed.fields if field.name == "來源")
        assert sent.kwargs["file"].filename == "meme.jpg"
    else:
        assert "找不到符合情境的梗圖" in sent.args[0]


def test_admin_permissions():
    async def run():
        event = interaction()
        cog = AdminCommands(Mock(), Mock(), {"i.imgur.com"})
        event.permissions = discord.Permissions.none()
        with pytest.raises(app_commands.MissingPermissions):
            await cog.meme_add._check_can_run(event)
        event.permissions = discord.Permissions(manage_guild=True)
        assert await cog.meme_add._check_can_run(event)

        event.permissions = discord.Permissions.none()
        with pytest.raises(app_commands.MissingPermissions):
            await cog.meme_ai_add._check_can_run(event)
        event.permissions = discord.Permissions(manage_guild=True)
        assert await cog.meme_ai_add._check_can_run(event)

        event.permissions = discord.Permissions.none()
        with pytest.raises(app_commands.MissingPermissions):
            await cog.meme_tag_add._check_can_run(event)
        event.permissions = discord.Permissions(manage_guild=True)
        assert await cog.meme_tag_add._check_can_run(event)

        event.permissions = discord.Permissions.none()
        with pytest.raises(app_commands.MissingPermissions):
            await cog.meme_edit._check_can_run(event)
        event.permissions = discord.Permissions(manage_guild=True)
        assert await cog.meme_edit._check_can_run(event)

        event.permissions = discord.Permissions.none()
        with pytest.raises(app_commands.MissingPermissions):
            await cog.meme_delete._check_can_run(event)
        event.permissions = discord.Permissions(manage_guild=True)
        assert await cog.meme_delete._check_can_run(event)

    asyncio.run(run())


def test_meme_ai_add_attachment_execution(monkeypatch):
    async def run():
        extract_mock = Mock(return_value=NS(tags=["tag_one", "tag_two", "tag_three"], description="用這句台詞回應讓人突然說不出話的場景。"))
        monkeypatch.setattr("cogs.admin_commands.analyze_image_with_gemini", extract_mock)
        register_mock = Mock()
        monkeypatch.setattr("cogs.admin_commands.register_new_tags", register_mock)

        database = Mock()
        database.add.return_value = 99
        cog = AdminCommands(database, Mock(), {"i.imgur.com"})

        attachment = Mock(spec=discord.Attachment)
        attachment.content_type = "image/png"
        attachment.size = 1024
        attachment.read = AsyncMock(return_value=b"fake_bytes")
        attachment.url = "https://cdn.discordapp.com/attachments/1/2/meme.png"

        event = interaction()
        await cog.meme_ai_add.callback(cog, event, attachment, None, "測試描述")

        event.response.defer.assert_awaited_once()
        extract_mock.assert_called_once_with(b"fake_bytes", "image/png", description="測試描述")
        # 應寫入全域圖庫 guild_id = 0
        database.add.assert_called_once()
        args = database.add.call_args[0]
        assert args[0] == 0  # GLOBAL_GUILD_ID
        assert args[1].image_url == attachment.url
        assert args[1].tags == ("tag_one", "tag_two", "tag_three")
        assert args[1].description == "測試描述"
        register_mock.assert_not_called()
        event.followup.send.assert_awaited_once()
        sent_msg = event.followup.send.call_args[0][0]
        assert "#99" in sent_msg
        assert "tag_one、tag_two、tag_three" in sent_msg

    asyncio.run(run())


@pytest.mark.parametrize("manual_description", [None, "", "   "])
def test_meme_ai_add_saves_generated_description(monkeypatch, tmp_path, manual_description):
    from services.analyzer import ImageAnalysisResult

    analysis = ImageAnalysisResult(
        tags=["speechless", "confused"], description="用無言的表情回應讓人摸不著頭緒的發言。",
    )
    analyze = Mock(return_value=analysis)
    monkeypatch.setattr("cogs.admin_commands.analyze_image_with_gemini", analyze)
    monkeypatch.setattr("cogs.admin_commands.register_new_tags", Mock())
    database = Database(tmp_path / "memes.db")
    database.initialize()
    cog = AdminCommands(database, Mock(), set())
    attachment = Mock(spec=discord.Attachment, content_type="image/png", size=8)
    attachment.read = AsyncMock(return_value=b"fake_png")
    attachment.url = "https://cdn.discordapp.com/attachments/1/2/meme.png"
    event = interaction()

    asyncio.run(cog.meme_ai_add.callback(cog, event, attachment, None, manual_description))

    analyze.assert_called_once_with(b"fake_png", "image/png", description="")
    assert database.get(0, 1).description == analysis.description
    assert f"AI 生成說明：{analysis.description}" in event.followup.send.call_args.args[0]


def test_meme_ai_add_url_execution(monkeypatch):
    async def run():
        extract_mock = Mock(return_value=NS(tags=["cat_meme", "surprised"], description="用貓咪驚訝的表情回應令人意外的消息。"))
        monkeypatch.setattr("cogs.admin_commands.analyze_image_with_gemini", extract_mock)
        register_mock = Mock()
        monkeypatch.setattr("cogs.admin_commands.register_new_tags", register_mock)

        real_async_client = httpx.AsyncClient

        def fake_client_factory(*args, **kwargs):
            def handler(request):
                return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=b"jpeg_data")
            return real_async_client(transport=httpx.MockTransport(handler))

        monkeypatch.setattr("cogs.admin_commands.httpx.AsyncClient", fake_client_factory)

        database = Mock()
        database.add.return_value = 101
        cog = AdminCommands(database, Mock(), {"i.imgur.com"})

        event = interaction()
        url = "https://i.imgur.com/cat.jpg"
        await cog.meme_ai_add.callback(cog, event, None, url, "貓咪梗圖")

        event.response.defer.assert_awaited_once()
        extract_mock.assert_called_once_with(b"jpeg_data", "image/jpeg", description="貓咪梗圖")
        database.add.assert_called_once()
        args = database.add.call_args[0]
        assert args[0] == 0
        assert args[1].image_url == url
        assert args[1].tags == ("cat_meme", "surprised")
        register_mock.assert_not_called()
        event.followup.send.assert_awaited_once()
        assert "#101" in event.followup.send.call_args[0][0]

    asyncio.run(run())


def test_meme_ai_add_url_unauthorized_host():
    async def run():
        cog = AdminCommands(Mock(), Mock(), {"i.imgur.com"})
        event = interaction()
        # 非白名單主機
        await cog.meme_ai_add.callback(cog, event, None, "https://evil.example.com/a.png")
        event.response.defer.assert_not_awaited()
        event.followup.send.assert_awaited_once()
        assert "請使用允許的圖片主機" in event.followup.send.call_args[0][0]

    asyncio.run(run())


def test_meme_ai_add_missing_or_ambiguous_parameters():
    async def run():
        cog = AdminCommands(Mock(), Mock(), {"i.imgur.com"})
        
        # 1. 兩者皆未提供
        event1 = interaction()
        await cog.meme_ai_add.callback(cog, event1, None, None)
        event1.followup.send.assert_awaited_once()
        assert "請提供圖片附件或圖片網址其中一項" in event1.followup.send.call_args[0][0]

        # 2. 兩者同時提供
        event2 = interaction()
        attachment = Mock(spec=discord.Attachment)
        await cog.meme_ai_add.callback(cog, event2, attachment, "https://i.imgur.com/a.png")
        event2.followup.send.assert_awaited_once()
        assert "請提供圖片附件或圖片網址其中一項" in event2.followup.send.call_args[0][0]

    asyncio.run(run())


def test_meme_tag_add_merges_and_registers_specific_tags(monkeypatch, tmp_path):
    catalog = tmp_path / "known_tags.json"
    register_new_tags(["speechless", "unrelated"], catalog)
    monkeypatch.setattr("cogs.admin_commands.register_new_tags", partial(register_new_tags, file_path=catalog))
    database = Database(tmp_path / "memes.db")
    database.initialize()
    url = "https://i.imgur.com/a.jpg"
    meme_id = database.add(0, Meme(url, ("speechless", "panic"), "原始描述", url))
    other_id = database.add(2, Meme(url, ("panic",), "其他伺服器", url))
    cog = AdminCommands(database, Mock(), set())

    event = interaction()
    asyncio.run(cog.meme_tag_add.callback(cog, event, meme_id, "Girls_Band_Cry, TOMO_EBISU girls_band_cry speechless"))

    meme = database.get(0, meme_id)
    assert meme.tags == ("speechless", "panic", "girls_band_cry", "tomo_ebisu")
    assert meme.description == "原始描述"
    assert load_known_tags(catalog) == ["speechless", "unrelated", "girls_band_cry", "tomo_ebisu"]
    assert " ".join(meme.tags) in event.followup.send.call_args.args[0]

    for unavailable_id in (other_id, 999):
        missing = interaction()
        asyncio.run(cog.meme_tag_add.callback(cog, missing, unavailable_id, "new_character"))
        assert f"找不到編號 #{unavailable_id}" in missing.followup.send.call_args.args[0]
    assert "new_character" not in load_known_tags(catalog)


def test_meme_edit_execution(monkeypatch):
    async def run():
        register_mock = Mock()
        monkeypatch.setattr("cogs.admin_commands.register_new_tags", register_mock)

        database = Mock()
        database.update_tags.return_value = True
        cog = AdminCommands(database, Mock(), {"i.imgur.com"})

        # 1. 成功修改標籤
        event = interaction()
        await cog.meme_edit.callback(cog, event, 12, "speechless, panic")
        event.response.defer.assert_awaited_once()
        database.update_tags.assert_called_once_with(12, ("speechless", "panic"))
        register_mock.assert_called_once_with(("speechless", "panic"))
        event.followup.send.assert_awaited_once()
        assert "已成功更新梗圖 #12" in event.followup.send.call_args[0][0]

        # 2. 標籤格式錯誤攔截
        event_bad = interaction()
        await cog.meme_edit.callback(cog, event_bad, 12, "INVALID-TAG")
        event_bad.response.defer.assert_not_awaited()
        assert "請提供 1 至 10 個以逗號分隔的英文 snake_case 標籤" in event_bad.followup.send.call_args[0][0]

        # 3. 查無此 meme_id
        database.update_tags.return_value = False
        event_missing = interaction()
        await cog.meme_edit.callback(cog, event_missing, 999, "doge")
        assert "找不到編號 #999" in event_missing.followup.send.call_args[0][0]

    asyncio.run(run())


def test_meme_delete_execution():
    async def run():
        database = Mock()
        database.delete.return_value = True
        cog = AdminCommands(database, Mock(), {"i.imgur.com"})

        # 1. 成功刪除
        event = interaction()
        await cog.meme_delete.callback(cog, event, 42)
        event.response.defer.assert_awaited_once()
        database.delete.assert_called_once_with(42)
        assert "已刪除梗圖 #42" in event.followup.send.call_args[0][0]

        # 2. 查無此 meme_id
        database.delete.return_value = False
        event_missing = interaction()
        await cog.meme_delete.callback(cog, event_missing, 999)
        assert "找不到編號 #999" in event_missing.followup.send.call_args[0][0]

    asyncio.run(run())
