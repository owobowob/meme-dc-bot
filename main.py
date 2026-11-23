import asyncio
import logging
import os
import sys

import discord
import httpx
from discord.ext import commands

from cogs.admin_commands import AdminCommands
from cogs.meme_commands import MemeCommands
from config import PROJECT_ROOT, startup_token
from services.database import Database, DatabaseError
from services.discord_service import Recommendations, command_error
from services.meme_search import CommonsSearch, MemeSearch
from services.memestack import MemeStackSearch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class MemeBot(commands.Bot):
    def __init__(self, database: Database, client: httpx.AsyncClient, test_guild: int | None):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.message_content = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.database = database
        self.web_client = client
        self.test_guild = test_guild

    async def setup_hook(self):
        hosts = set(filter(None, (
            host.strip().lower() for host in
            os.getenv("IMAGE_HOSTS", "upload.wikimedia.org,i.imgur.com").split(",")
        )))
        web = CommonsSearch(self.web_client, os.getenv("WIKIMEDIA_CONTACT", "").strip())
        memestack_key = os.getenv("MEMESTACK_API_KEY", "").strip()
        if memestack_key:
            web = MemeStackSearch(self.web_client, memestack_key)
        search = MemeSearch(self.database, web, hosts)
        await self.add_cog(MemeCommands(Recommendations(search)))
        await self.add_cog(AdminCommands(self.database, self.web_client, hosts))
        self.tree.on_error = command_error
        guild = discord.Object(id=self.test_guild) if self.test_guild else None
        if guild:
            self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)


async def main():
    token = startup_token()
    test_guild_value = os.getenv("DISCORD_TEST_GUILD_ID", "").strip()
    if test_guild_value and (not test_guild_value.isdecimal() or int(test_guild_value) <= 0):
        raise ValueError("DISCORD_TEST_GUILD_ID 必須是有效的伺服器 ID。")
    test_guild = int(test_guild_value) if test_guild_value else None
    database = Database(PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/memes.db"))
    await asyncio.to_thread(database.initialize)
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        async with MemeBot(database, client, test_guild) as bot:
            await bot.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, DatabaseError) as exc:
        sys.exit(str(exc))
    except (discord.DiscordException, OSError):
        sys.exit("Discord 啟動失敗，請檢查 Token、網路、安裝權限及 Message Content Intent。")
    except KeyboardInterrupt:
        pass
