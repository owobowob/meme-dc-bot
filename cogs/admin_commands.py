import asyncio
import logging
import re
from textwrap import wrap

import discord
import httpx
from discord import app_commands
from discord.ext import commands

from models.meme import Meme
from services.analyzer import TagExtractionError, analyze_image_with_gemini
from services.database import GLOBAL_GUILD_ID, Database, DatabaseError
from services.discord_service import reply
from services.meme_search import IMAGE_TYPES, https_host, image_available
from services.tag_store import normalize_tag, register_new_tags

logger = logging.getLogger(__name__)


class AdminCommands(commands.Cog):
    def __init__(self, database: Database, client: httpx.AsyncClient, image_hosts: set[str] | frozenset[str]):
        self.database = database
        self.client = client
        self.image_hosts = image_hosts

    @app_commands.command(description="新增一張自建梗圖（管理伺服器權限）")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def meme_add(
        self, interaction: discord.Interaction, image_url: str, tags: str,
        description: app_commands.Range[str, 0, 500] = "",
    ):
        image_url = image_url.strip()
        if https_host(image_url) not in self.image_hosts:
            await reply(interaction, "請使用允許的圖片主機及直接 HTTPS 圖片網址。")
            return
        cleaned = tuple(dict.fromkeys(part.strip().lower() for part in tags.split(",")))
        if not 1 <= len(cleaned) <= 10 or any(
            len(tag) > 64 or not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", tag)
            for tag in cleaned
        ):
            await reply(interaction, "請提供 1 至 10 個以逗號分隔的英文 snake_case 標籤。")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await image_available(self.client, image_url, self.image_hosts):
            await reply(interaction, "無法確認圖片可用。請檢查直接圖片網址及格式。")
            return
        try:
            meme_id = await asyncio.to_thread(
                self.database.add, interaction.guild_id, Meme(image_url, cleaned, description, image_url)
            )
        except DatabaseError as exc:
            await reply(interaction, str(exc))
            return
        await reply(interaction, f"已新增梗圖 #{meme_id}。")

    @app_commands.command(description="上傳圖片或提供圖片網址，由 AI 自動辨識標籤寫入全域圖庫（管理伺服器權限）")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 10, key=lambda i: i.user.id)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def meme_ai_add(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment | None = None,
        image_url: str | None = None,
        description: app_commands.Range[str, 0, 500] = "",
    ):
        # 1. 驗證參數：必須二選一
        if (image is None and not image_url) or (image is not None and image_url):
            await reply(interaction, "請提供圖片附件或圖片網址其中一項。")
            return

        target_url: str
        image_bytes: bytes
        mime_type: str

        if image is not None:
            mime_type = (image.content_type or "").split(";")[0].lower().strip()
            if not mime_type.startswith("image/"):
                await reply(interaction, "請上傳有效的圖片檔案（JPEG、PNG、GIF、WEBP）。")
                return

            if image.size > 8 * 1024 * 1024:
                await reply(interaction, "圖片大小不可超過 8MB。")
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                image_bytes = await image.read()
            except (discord.HTTPException, OSError) as exc:
                logger.warning(f"讀取 Discord 上傳圖片附件失敗: {exc}", exc_info=True)
                await reply(interaction, f"讀取上傳圖片失敗：{exc}")
                return
            target_url = image.url
        else:
            clean_url = (image_url or "").strip()
            if https_host(clean_url) not in self.image_hosts:
                logger.warning(f"外部圖片網址非允許的主機白名單: {clean_url}")
                await reply(interaction, "請使用允許的圖片主機及直接 HTTPS 圖片網址。")
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                # 嚴格禁止 follow_redirects 以防 SSRF Redirect 繞過
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as dl_client:
                    resp = await dl_client.get(clean_url)
                    if resp.status_code != 200:
                        logger.warning(f"外部圖片下載失敗 ({clean_url})，遠端伺服器回應狀態碼: {resp.status_code}")
                        await reply(interaction, f"無法下載圖片，遠端伺服器回應狀態碼：{resp.status_code}。")
                        return

                    raw_content_type = resp.headers.get("content-type", "")
                    mime_type = raw_content_type.split(";")[0].lower().strip()
                    if not mime_type.startswith("image/"):
                        logger.warning(f"外部圖片 ({clean_url}) 回應非圖片格式 Content-Type: {raw_content_type}")
                        await reply(interaction, "該網址回傳的不是有效圖片格式。")
                        return

                    image_bytes = resp.content
                    if len(image_bytes) > 8 * 1024 * 1024:
                        logger.warning(f"外部圖片 ({clean_url}) 檔案大小超過 8MB ({len(image_bytes)} bytes)")
                        await reply(interaction, "圖片大小不可超過 8MB。")
                        return
            except httpx.TimeoutException:
                logger.warning(f"下載外部圖片超時 ({clean_url})")
                await reply(interaction, "下載外部圖片超時，請稍後再試。")
                return
            except httpx.HTTPError as exc:
                logger.warning(f"下載外部圖片發生 HTTP 錯誤 ({clean_url}): {exc}")
                await reply(interaction, "下載外部圖片失敗，請檢查網址連線。")
                return
            except Exception as exc:
                logger.exception(f"處理外部圖片時發生非預期錯誤 ({clean_url}): {exc}")
                await reply(interaction, "處理外部圖片時發生錯誤。")
                return
            target_url = clean_url

        manual_description = (description or "").strip()
        try:
            analysis = await asyncio.to_thread(
                analyze_image_with_gemini, image_bytes, mime_type, description=manual_description
            )
        except TagExtractionError as exc:
            await reply(interaction, str(exc))
            return

        saved_description = manual_description or analysis.description
        description_label = "說明" if manual_description else "AI 生成說明"
        try:
            meme_id = await asyncio.to_thread(
                self.database.add,
                GLOBAL_GUILD_ID,
                Meme(target_url, tuple(analysis.tags), saved_description, target_url),
            )
        except DatabaseError as exc:
            await reply(interaction, str(exc))
            return

        tags_str = "、".join(analysis.tags) or "無符合的已知標籤"
        await reply(
            interaction,
            f"成功新增全域梗圖 #{meme_id}！\nAI 標籤：{tags_str}"
            f"\n{description_label}：{saved_description}",
        )

    @app_commands.command(description="追加指定梗圖的專有標籤（管理伺服器權限）")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def meme_tag_add(
        self, interaction: discord.Interaction, meme_id: app_commands.Range[int, 1],
        tags: app_commands.Range[str, 1, 500],
    ):
        cleaned = tuple(dict.fromkeys(
            normalize_tag(part) for part in re.split(r"[\s,，]+", tags) if part
        ))
        if not cleaned or None in cleaned:
            await reply(interaction, "請提供以逗號或空白分隔的英文 snake_case 標籤，每個最多 35 字元，且不可使用停用詞。")
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            merged = await asyncio.to_thread(self.database.append_tags, interaction.guild_id, meme_id, cleaned)
        except DatabaseError as exc:
            await reply(interaction, str(exc))
            return
        if merged is None:
            await reply(interaction, f"找不到編號 #{meme_id} 的梗圖。")
            return

        catalog = await asyncio.to_thread(register_new_tags, cleaned)
        lines = wrap(" ".join(merged), width=1800, break_long_words=False, break_on_hyphens=False)
        await reply(interaction, f"梗圖 #{meme_id} 的完整標籤：\n{lines[0]}")
        for line in lines[1:]:
            await reply(interaction, line)
        missing = [tag for tag in cleaned if tag not in catalog]
        if missing:
            await reply(interaction, "已更新 SQLite，但以下標籤尚未同步至標籤庫，請檢查容量或檔案權限：" + "、".join(missing))

    @app_commands.command(description="修改指定梗圖的標籤（管理伺服器權限）")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def meme_edit(
        self,
        interaction: discord.Interaction,
        meme_id: app_commands.Range[int, 1, 1000000],
        tags: str,
    ):
        cleaned = tuple(dict.fromkeys(part.strip().lower() for part in tags.split(",")))
        if not 1 <= len(cleaned) <= 10 or any(
            len(tag) > 64 or not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", tag)
            for tag in cleaned
        ):
            await reply(interaction, "請提供 1 至 10 個以逗號分隔的英文 snake_case 標籤。")
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            success = await asyncio.to_thread(self.database.update_tags, meme_id, cleaned)
        except DatabaseError as exc:
            await reply(interaction, str(exc))
            return

        if not success:
            await reply(interaction, f"找不到編號 #{meme_id} 的梗圖。")
            return

        await asyncio.to_thread(register_new_tags, cleaned)
        tags_str = "、".join(cleaned)
        await reply(interaction, f"已成功更新梗圖 #{meme_id} 的標籤為：{tags_str}")

    @app_commands.command(description="刪除指定編號的梗圖（管理伺服器權限）")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 5, key=lambda i: i.user.id)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def meme_delete(
        self,
        interaction: discord.Interaction,
        meme_id: app_commands.Range[int, 1, 1000000],
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            success = await asyncio.to_thread(self.database.delete, meme_id)
        except DatabaseError as exc:
            await reply(interaction, str(exc))
            return

        if not success:
            await reply(interaction, f"找不到編號 #{meme_id} 的梗圖。")
            return

        await reply(interaction, f"已刪除梗圖 #{meme_id}。")
