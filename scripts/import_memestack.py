"""批量採集 MemeStack 熱門迷因並匯入全域圖庫 (guild_id = 0)。"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.meme import Meme
from services.database import GLOBAL_GUILD_ID, Database, DuplicateMeme
from services.meme_search import https_host
from services.tag_store import load_known_tags, register_new_tags


async def import_from_memestack(limit_per_tag: int = 2):
    api_key = os.getenv("MEMESTACK_API_KEY", "").strip()
    if not api_key:
        print("[!] 錯誤：未設定 MEMESTACK_API_KEY，無法從 MemeStack 採集。")
        return

    db_path = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/memes.db")
    database = Database(db_path)
    database.initialize()

    tags_to_query = load_known_tags()
    print(f"[*] 開始從 MemeStack 採集標籤（共 {len(tags_to_query)} 個標籤）...")

    imported_count = 0
    newly_discovered_tags: set[str] = set()

    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        for tag in tags_to_query:
            query = tag.replace("_", " ")
            try:
                response = await client.get(
                    "https://api.memestack.ai/v2/search",
                    headers={"X-API-Key": api_key},
                    params={"q": query, "limit": limit_per_tag},
                )
                if response.status_code == 429:
                    print("[!] 遭遇速率限制 (429)，等待 5 秒...")
                    await asyncio.sleep(5)
                    continue
                if response.status_code in (401, 403):
                    print("[!] API Key 無效或未授權。")
                    break

                response.raise_for_status()
                payload = response.json()

                # 過濾低關聯結果
                match_quality = payload.get("memestack", {}).get("match_quality")
                if match_quality in {"closest_only", "none"}:
                    continue

                for item in payload.get("results", []):
                    image_url = item.get("media_formats", {}).get("gif", {}).get("url", "")
                    source = item.get("itemurl", "")
                    if (
                        https_host(image_url) == "api.memestack.ai"
                        and https_host(source) == "memestack.ai"
                    ):
                        meme = Meme(
                            image_url=image_url,
                            tags=(tag,),
                            description=item.get("content_description", "")[:400],
                            source=source,
                        )
                        try:
                            database.add(GLOBAL_GUILD_ID, meme)
                            imported_count += 1
                            newly_discovered_tags.add(tag)
                            print(f"[+] 匯入全域迷因：{tag} -> {image_url}")
                        except DuplicateMeme:
                            pass
            except Exception as exc:
                print(f"[!] 查詢標籤 '{tag}' 失敗：{exc}")

            await asyncio.sleep(0.5)

    if newly_discovered_tags:
        register_new_tags(newly_discovered_tags)

    print(f"[✔] MemeStack 匯入完成！成功新增 {imported_count} 張梗圖。")


if __name__ == "__main__":
    asyncio.run(import_from_memestack())
