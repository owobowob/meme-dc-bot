import discord
from discord import app_commands
from discord.ext import commands

from services.discord_service import Recommendations, recent_context, reply


class MemeCommands(commands.Cog):
    def __init__(self, recommendations: Recommendations):
        self.recommendations = recommendations

    @app_commands.command(description="分析指定文字並推薦梗圖")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 20, key=lambda i: i.user.id)
    async def meme(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 1800]):
        if not text.strip():
            await reply(interaction, "請輸入要分析的文字。")
            return
        await interaction.response.defer(thinking=True)
        await self.recommendations.send(interaction, text, include_recent=True)

    @app_commands.command(description="將此頻道最近 12 則訊息中的聊天文字交給 AI 分析")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 30, key=lambda i: i.user.id)
    async def meme_recent(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            context = await recent_context(interaction)
        except discord.HTTPException:
            await reply(interaction, "無法讀取頻道歷史，請檢查頻道權限或稍後再試。")
            return
        if not context:
            await reply(interaction, "沒有可分析的文字。請檢查頻道權限與 Message Content Intent。")
            return
        await self.recommendations.send(interaction, context)

    @app_commands.command(description="顯示梗圖 Bot 指令")
    async def meme_help(self, interaction: discord.Interaction):
        await reply(interaction, "\n".join((
            "**/meme** text：將指定文字交給目前的 AI 供應商分析。",
            "**/meme_recent**：將最近 12 則訊息中的聊天文字交給 AI 分析。",
            "**/meme_add** image_url tags [description]：管理者可加入圖片，標籤以逗號分隔。",
            "**/meme_ai_add** [image] [image_url] [description]：管理者上傳圖片或提供圖片網址，由 AI 自動辨識標籤並加入全域圖庫。",
            "**/meme_edit** meme_id tags：管理者修改指定編號梗圖的標籤。",
            "**/meme_delete** meme_id：管理者刪除指定編號的梗圖。",
            "**/meme_help**：顯示這份說明。",
        )))
