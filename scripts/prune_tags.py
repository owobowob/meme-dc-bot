"""標籤詞頻統計與資料治理清理腳本。

統計 data/memes.db 中的實際標籤詞頻，移除無效標籤與停用詞，
並將 data/known_tags.json 精簡保留至約 110 個高頻核心標籤。
"""
from __future__ import annotations

from collections import Counter
import json
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.tag_store import (
    DEFAULT_FALLBACK_TAGS,
    STOP_TAGS,
    TAG_REGEX,
    TAGS_FILE,
    is_valid_tag,
    normalize_tag,
)

TARGET_TAG_COUNT = 110


def prune_tags(
    db_path: Path = PROJECT_ROOT / "data" / "memes.db",
    tags_file: Path = TAGS_FILE,
    target_count: int = TARGET_TAG_COUNT,
) -> list[str]:
    print(f"[*] 開始執行標籤治理維護...")
    tag_counter: Counter[str] = Counter()

    # 1. 統計 SQLite 資料庫中既有標籤詞頻
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT tags FROM memes")
                for (tags_json,) in cursor.fetchall():
                    try:
                        tags = json.loads(tags_json)
                        if isinstance(tags, list):
                            for tag in tags:
                                norm = normalize_tag(str(tag))
                                if norm:
                                    tag_counter[norm] += 1
                    except (json.JSONDecodeError, TypeError):
                        continue
            print(f"[+] 自資料庫統計了 {len(tag_counter)} 種標籤出現次數。")
        except sqlite3.Error as exc:
            print(f"[!] 讀取資料庫時發生警告：{exc}")
    else:
        print(f"[-] 資料庫 {db_path} 尚未建立，依既有標籤庫進行清理。")

    # 2. 讀取目前 known_tags.json
    existing_tags: list[str] = []
    if tags_file.exists():
        try:
            raw = json.loads(tags_file.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for item in raw:
                    norm = normalize_tag(str(item))
                    if norm and norm not in existing_tags:
                        existing_tags.append(norm)
        except (json.JSONDecodeError, OSError):
            pass

    # 若現有標籤庫為空，載入預設 fallback
    all_candidates = set(existing_tags) | set(DEFAULT_FALLBACK_TAGS) | set(tag_counter.keys())

    # 3. 嚴格過濾黑名單、無效格式與過長字串
    valid_candidates = {tag for tag in all_candidates if is_valid_tag(tag)}

    # 4. 排序權重：資料庫詞頻優先，次之為原本在 known_tags 中的順序
    def sort_key(tag: str) -> tuple[int, int, str]:
        freq = tag_counter.get(tag, 0)
        is_known = 1 if tag in existing_tags else 0
        return (-freq, -is_known, tag)

    ranked_tags = sorted(valid_candidates, key=sort_key)
    pruned_tags = ranked_tags[:target_count]

    # 5. 寫回 known_tags.json
    tags_file.parent.mkdir(parents=True, exist_ok=True)
    tags_file.write_text(
        json.dumps(pruned_tags, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[✔] 治理完成！共保留 {len(pruned_tags)} 個核心標籤，已儲存至 {tags_file}。")
    return pruned_tags


if __name__ == "__main__":
    prune_tags()
