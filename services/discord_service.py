import asyncio

import httpx

import discord
from discord import app_commands

from services.analyzer import TagExtractionError, analyze_chat_search
from services.database import DatabaseError
from services.image_download import download_image
from services.meme_search import MemeSearch, SearchError, https_host


async def reply(interaction: discord.Interaction, message: str):
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        message = f"請等待 {error.retry_after:.0f} 秒後再試。"
    elif isinstance(error, app_commands.CheckFailure):
        message = "你沒有使用這個指令的權限。"
    else:
        message = "指令執行失敗，請稍後再試。"
    await reply(interaction, message)


async def recent_context(interaction: discord.Interaction) -> str:
    channel = interaction.channel
    if (not interaction.permissions.read_message_history
            or not interaction.app_permissions.read_message_history
            or not isinstance(channel, (discord.TextChannel, discord.Thread))):
        return ""
    lines = []
    async for message in channel.history(limit=12):
        if message.author.bot or message.webhook_id or not message.content.strip():
            continue
        lines.append(message.content.strip()[:300])
    return "\n".join(f"訊息 {index}：{line}" for index, line in enumerate(reversed(lines), 1))


class Recommendations:
    def __init__(self, search: MemeSearch):
        self.search = search
        self.slots = asyncio.Semaphore(3)

    async def send(self, interaction: discord.Interaction, text: str, *, include_recent: bool = False):
        if self.slots.locked():
            await reply(interaction, "目前有多筆分析正在執行，請稍後再試。")
            return

        try:
            async with self.slots:
                result = await asyncio.to_thread(analyze_chat_search, text)
                tags = result.matched_known_tags or result.fallback_search_tags
                context = text
                if include_recent and result.matched_known_tags:
                    try:
                        history = await recent_context(interaction)
                    except discord.HTTPException:
                        history = ""
                    if history:
                        context = f"{text}\n{history}"
                meme = await self.search.search(
                    interaction.guild_id, result.matched_known_tags, context=context,
                    fallback_tags=result.fallback_search_tags,
                )
        except (TagExtractionError, DatabaseError, SearchError) as exc:
            await reply(interaction, str(exc))
            return

        if meme is None:
            await reply(interaction, "找不到符合情境的梗圖。換個說法或補充一句台詞再試試。")
            return

        embed = discord.Embed(title="梗圖推薦", description=meme.description or None, url=meme.source)
        embed.add_field(name="分析標籤" if result.matched_known_tags else "搜尋關鍵字", value="、".join(tags), inline=False)
        image_tags = "、".join(meme.tags)
        if len(image_tags) > 1024:
            image_tags = image_tags[:1023] + "…"
        embed.add_field(name="圖片標籤", value=image_tags or "無", inline=False)
        
        # 來源顯示透明化：本地快取/資料庫命中標記
        base_source_label = "via MemeStack" if https_host(meme.source) == "memestack.ai" else "開啟來源"
        if meme.id is not None:
            source_label = f"{base_source_label}（本地資料庫 #{meme.id}）"
        else:
            source_label = f"外部搜尋 · {base_source_label}"

        embed.add_field(name="來源", value=f"[{source_label}]({meme.source})", inline=False)
        options = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
        if base_source_label == "via MemeStack" and https_host(meme.image_url) == "api.memestack.ai":
            image = await memestack_attachment(self.search.web.client, meme.image_url)
            if image:
                options["file"] = image
                embed.set_image(url=f"attachment://{image.filename}")
            else:
                embed.description = "暫時無法載入圖片，可透過來源連結查看。"
        else:
            embed.set_image(url=meme.image_url)
        try:
            await interaction.followup.send(**options)
        except discord.Forbidden:
            if "file" not in options:
                raise
            del options["file"]
            embed.set_image(url=None)
            embed.description = "Bot 無法上傳圖片，請檢查此頻道的 Attach Files（附加檔案）權限。"
            await interaction.followup.send(**options)


async def memestack_attachment(client: httpx.AsyncClient, url: str) -> discord.File | None:
    extensions = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
    result = await download_image(client, url, extensions)
    if result is None:
        return None
    data, mime_type = result
    return discord.File(data, filename=f"meme.{extensions[mime_type]}")
