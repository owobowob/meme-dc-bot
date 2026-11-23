"""串接 Imgflip API 批量抓取前 100 熱門模板並利用 Gemini 多模態生成精準標籤入庫。"""
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
from services.analyzer import extract_tags_from_image
from services.database import GLOBAL_GUILD_ID, Database, DuplicateMeme
from services.tag_store import register_new_tags


async def import_from_imgflip(max_items: int = 100):
    """
    提醒：請確保在 .env 的 IMAGE_HOSTS 中加入 'i.imgflip.com'，
    例如：IMAGE_HOSTS=upload.wikimedia.org,i.imgur.com,i.imgflip.com
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("[!] 錯誤：未設定 GEMINI_API_KEY，無法利用多模態視覺分析梗圖。")
        return

    db_path = PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/memes.db")
    database = Database(db_path)
    database.initialize()

    print("[*] 正在從 Imgflip 獲取熱門 Meme 模板 (https://api.imgflip.com/get_memes)...")
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        try:
            resp = await client.get("https://api.imgflip.com/get_memes")
            resp.raise_for_status()
            data = resp.json()
            if not data.get("success"):
                print("[!] Imgflip API 回傳錯誤。")
                return
            memes_data = data.get("data", {}).get("memes", [])[:max_items]
        except Exception as exc:
            print(f"[!] 無法取得 Imgflip 迷因清單：{exc}")
            return

        print(f"[+] 成功獲取 {len(memes_data)} 個模板，準備進行多模態辨識與入庫...")
        imported = 0

        for item in memes_data:
            name = item.get("name", "")
            url = item.get("url", "")
            if not url.startswith("https://"):
                continue

            try:
                # 下載圖片以進行視覺模型分析
                img_resp = await client.get(url)
                if img_resp.status_code != 200:
                    continue
                mime_type = img_resp.headers.get("content-type", "image/jpeg").split(";")[0].lower()
                image_bytes = img_resp.content

                # 透過 Gemini 多模態看圖抽取 3～5 個精準標籤
                tags = await asyncio.to_thread(extract_tags_from_image, image_bytes, mime_type)
                if not tags:
                    continue

                meme = Meme(
                    image_url=url,
                    tags=tuple(tags),
                    description=name[:300],
                    source=url,
                )
                try:
                    database.add(GLOBAL_GUILD_ID, meme)
                    register_new_tags(tags)
                    imported += 1
                    print(f"[+] #{imported} 匯入成功：{name} -> 標籤：{tags}")
                except DuplicateMeme:
                    print(f"[-] 略過已存在迷因：{name}")

                # 避免觸發 API 頻率限制
                await asyncio.sleep(1.0)
            except Exception as exc:
                print(f"[!] 處理迷因 '{name}' 時發生錯誤：{exc}")

    print(f"\n[✔] Imgflip 匯入完成！共新增 {imported} 張梗圖至全域圖庫 (guild_id = 0)。")
    print("[TIP] 提示：請確認 .env 中的 IMAGE_HOSTS 包含 'i.imgflip.com' 以便正常於 Discord 顯示！")


if __name__ == "__main__":
    asyncio.run(import_from_imgflip())
