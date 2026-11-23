"""依人氣從 MyGO JSON API 匯入全域梗圖。"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models.meme import Meme
from services.analyzer import TagExtractionError, extract_tags_from_image
from services.database import GLOBAL_GUILD_ID, Database, DatabaseError, DuplicateMeme
from services.image_download import download_image
from services.meme_search import IMAGE_TYPES, https_host
from services.tag_store import normalize_tag, register_work_tag

STORAGE_HOST = "storageapi.miyago9267.com"


@dataclass
class ImportStats:
    added: int = 0
    skipped: int = 0
    failed: int = 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=os.getenv("MYGO_API_URL", "").strip(), help="MyGO JSON API HTTPS 端點")
    parser.add_argument("--top", type=int, default=50, help="匯入前 N 筆，0 為全部（預設 50）")
    parser.add_argument("--min-popularity", type=int, default=0, help="最低人氣（預設 0）")
    parser.add_argument("--extra-tag", default="mygo", help="附加的已知標籤（預設 mygo）")
    parser.add_argument("--dry-run", action="store_true", help="只顯示前 10 筆排序預覽，不寫入資料")
    args = parser.parse_args(argv)
    if not https_host(args.api_url):
        parser.error("請透過 --api-url 或 MYGO_API_URL 指定有效的 HTTPS API 端點。")
    if args.top < 0:
        parser.error("--top 必須大於等於 0。")
    args.extra_tag = normalize_tag(args.extra_tag)
    if args.extra_tag is None:
        parser.error("--extra-tag 必須是合法的英文 snake_case 標籤。")
    return args


async def fetch_images(client: httpx.AsyncClient, api_url: str) -> list[dict]:
    items = []
    page = 1
    limit = 2000
    while True:
        response = await client.get(api_url, params={"page": page, "limit": limit})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("MyGO API 回傳的資料格式錯誤。")
        batch, meta = payload.get("data"), payload.get("meta")
        if (not isinstance(batch, list) or any(not isinstance(item, dict) for item in batch)
                or not isinstance(meta, dict) or not isinstance(meta.get("hasNext"), bool)
                or meta.get("page", page) != page):
            raise ValueError(f"MyGO API 第 {page} 頁的資料或分頁格式錯誤。")
        items.extend(batch)
        if not meta["hasNext"]:
            return items
        if not batch:
            raise ValueError(f"MyGO API 第 {page} 頁為空，但仍標示有下一頁。")
        if page == 1:
            # 固定後續頁面的實際頁長，避免伺服器將 2000 截短時跳過圖片。
            limit = min(limit, len(batch))
        page += 1
        await asyncio.sleep(0.3)


def popularity(item: dict) -> int:
    score = item.get("popularity", 0)
    return score if type(score) is int else 0


def select_images(items: list[dict], top: int, min_popularity: int) -> list[dict]:
    ranked = sorted(items, key=popularity, reverse=True)
    selected = [item for item in ranked if popularity(item) >= min_popularity]
    return selected[:top] if top else selected


def clean_text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def format_description(item: dict) -> str:
    tags = item.get("tags")
    characters = ""
    if isinstance(tags, list):
        characters = "、".join(dict.fromkeys(text for tag in tags if (text := clean_text(tag))))
    alt = clean_text(item.get("alt"))
    scene = clean_text(item.get("description"))
    fields = (
        ("出處", clean_text(item.get("episode"))),
        ("角色", characters),
        ("情境", scene if scene != alt else ""),
        ("標題／台詞", alt),
    )
    return "｜".join(f"{label}：{value}" for label, value in fields if value)


async def import_images(
    client: httpx.AsyncClient, database: Database, items: list[dict], extra_tag: str,
) -> ImportStats:
    stats = ImportStats()
    for item in items:
        url = clean_text(item.get("url"))
        label = clean_text(item.get("alt")) or url or f"ID {item.get('id', '?')}"
        is_new = False
        try:
            if url and await asyncio.to_thread(database.contains_url, url):
                stats.skipped += 1
                print(f"[SKIP] 已存在：{label}")
                continue
            is_new = True
            if https_host(url) != STORAGE_HOST:
                raise ValueError("圖片網址不屬於 MyGO HTTPS 圖床。")
            image = await download_image(client, url, IMAGE_TYPES)
            if image is None:
                raise ValueError("圖片下載失敗或超出大小／格式限制。")
            data, mime_type = image
            description = format_description(item)
            with data:
                tags = await asyncio.to_thread(
                    extract_tags_from_image, data.getvalue(), mime_type, description=description,
                )
            merged_tags = tuple(dict.fromkeys((*tags, extra_tag)))
            await asyncio.to_thread(database.add, GLOBAL_GUILD_ID, Meme(url, merged_tags, description, url))
        except DuplicateMeme:
            stats.skipped += 1
            print(f"[SKIP] 已存在：{label}")
        except (DatabaseError, TagExtractionError, ValueError) as exc:
            stats.failed += 1
            print(f"[FAIL] {label}：{exc}")
        else:
            stats.added += 1
            print(f"[ADD] {label}")
        finally:
            if is_new:
                await asyncio.sleep(1.5)
    return stats


async def run(args: argparse.Namespace) -> int:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            items = await fetch_images(client, args.api_url)
            selected = select_images(items, args.top, args.min_popularity)
            print(f"API 共 {len(items)} 筆，符合匯入條件 {len(selected)} 筆。")
            if args.dry_run:
                for rank, item in enumerate(selected[:10], 1):
                    print(f"{rank:>2}. 人氣 {popularity(item)}｜{format_description(item)}｜{item.get('url', '')}")
                return 0

            stats = ImportStats()
            if selected:
                if not os.getenv("GEMINI_API_KEY", "").strip():
                    raise ValueError("請設定 GEMINI_API_KEY 後再執行匯入。")
                database = Database(PROJECT_ROOT / os.getenv("DATABASE_PATH", "data/memes.db"))
                await asyncio.to_thread(database.initialize)
                if not await asyncio.to_thread(register_work_tag, args.extra_tag):
                    raise ValueError("無法註冊附加標籤，請檢查標籤庫容量及寫入權限。")
                stats = await import_images(client, database, selected, args.extra_tag)
    except (httpx.HTTPError, DatabaseError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    print(f"完成：成功新增 {stats.added}／已存在跳過 {stats.skipped}／失敗 {stats.failed}")
    return int(stats.failed > 0)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
