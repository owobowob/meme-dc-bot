"""動態管理已知標籤庫與品質防火牆。"""
from __future__ import annotations

import json
from pathlib import Path
import re
import threading
from contextlib import suppress
from tempfile import NamedTemporaryFile
from typing import Iterable

from config import PROJECT_ROOT

TAGS_FILE = PROJECT_ROOT / "data" / "known_tags.json"
WORK_TAGS_FILE = TAGS_FILE.with_name("work_tags.json")
MAX_KNOWN_TAGS = 150
TAG_REGEX = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

STOP_TAGS = frozenset({
    "meme",
    "memes",
    "photograph",
    "photo",
    "image",
    "picture",
    "pic",
    "funny",
    "reaction",
    "template",
    "personalities",
    "personality",
    "animal",
    "animals",
})

DEFAULT_FALLBACK_TAGS = []

_lock = threading.Lock()


def is_valid_tag(tag: str) -> bool:
    """檢查單一標籤是否合法且不為停用詞（必須完全符合小寫 snake_case）。"""
    if not isinstance(tag, str):
        return False
    if not tag or len(tag) > 35:
        return False
    if tag in STOP_TAGS:
        return False
    return bool(TAG_REGEX.fullmatch(tag))


def normalize_tag(tag: str) -> str | None:
    """將標籤正規化為小寫 snake_case，若不合法則回傳 None。"""
    if not isinstance(tag, str):
        return None
    cleaned = tag.strip().lower().replace(" ", "_")
    return cleaned if is_valid_tag(cleaned) else None


def load_known_tags(file_path: Path = TAGS_FILE) -> list[str]:
    """讀取已知標籤清單，檔案不存在或損壞時回傳預設標籤。"""
    with _lock:
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                valid_tags: list[str] = []
                seen = set()
                for item in data:
                    norm = normalize_tag(str(item))
                    if norm and norm not in seen:
                        seen.add(norm)
                        valid_tags.append(norm)
                return valid_tags
        except (json.JSONDecodeError, OSError):
            pass
        return list(DEFAULT_FALLBACK_TAGS)


def register_new_tags(tags: Iterable[str], file_path: Path = TAGS_FILE) -> list[str]:
    """收錄有效新標籤至已知標籤庫，遵守品質防火牆與 MAX_KNOWN_TAGS 上限。"""
    with _lock:
        current_tags: list[str] = []
        if file_path.exists():
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        norm = normalize_tag(str(item))
                        if norm and norm not in current_tags:
                            current_tags.append(norm)
            except (json.JSONDecodeError, OSError):
                current_tags = list(DEFAULT_FALLBACK_TAGS)
        else:
            current_tags = list(DEFAULT_FALLBACK_TAGS)

        added = []
        for tag in tags:
            norm = normalize_tag(tag)
            if norm and norm not in current_tags:
                if len(current_tags) < MAX_KNOWN_TAGS:
                    current_tags.append(norm)
                    added.append(norm)

        if added:
            temp_path = None
            try:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(mode="w", dir=file_path.parent, encoding="utf-8", delete=False) as temp:
                    temp_path = Path(temp.name)
                    json.dump(current_tags, temp, ensure_ascii=False, indent=2)
                temp_path.replace(file_path)
            except OSError:
                return [tag for tag in current_tags if tag not in added]
            finally:
                if temp_path is not None:
                    with suppress(OSError):
                        temp_path.unlink(missing_ok=True)

        return current_tags


def load_work_tags(file_path: Path = WORK_TAGS_FILE) -> set[str]:
    return {"mygo", *load_known_tags(file_path)}


def register_work_tag(tag: str) -> bool:
    # Record the classification before exposing the tag in the known catalog.
    return (tag in register_new_tags([tag], WORK_TAGS_FILE)
            and tag in register_new_tags([tag]))
