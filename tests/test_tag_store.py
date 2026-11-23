import json
from pathlib import Path
import pytest

from services.tag_store import (
    MAX_KNOWN_TAGS,
    STOP_TAGS,
    is_valid_tag,
    load_known_tags,
    normalize_tag,
    register_new_tags,
)


def test_is_valid_tag():
    assert is_valid_tag("doge") is True
    assert is_valid_tag("this_is_fine") is True
    assert is_valid_tag("cat_123") is True

    # 停用詞黑名單
    for stop_tag in ["meme", "memes", "funny", "photo", "image", "reaction", "animal"]:
        assert is_valid_tag(stop_tag) is False

    # 格式不符或長度過長
    assert is_valid_tag("Cat") is False
    assert is_valid_tag("invalid-tag") is False
    assert is_valid_tag("invalid__tag") is False
    assert is_valid_tag("123_tag") is False
    assert is_valid_tag("a" * 36) is False  # > 35 chars
    assert is_valid_tag("") is False


def test_normalize_tag():
    assert normalize_tag("  Doge  ") == "doge"
    assert normalize_tag("this is fine") == "this_is_fine"
    assert normalize_tag("funny") is None
    assert normalize_tag("bad-tag") is None


def test_register_and_max_capacity(tmp_path):
    tag_file = tmp_path / "known_tags.json"
    init_tags = [f"tag_{i}" for i in range(MAX_KNOWN_TAGS - 2)]
    tag_file.write_text(json.dumps(init_tags), encoding="utf-8")

    # 註冊有效標籤與無效標籤
    res = register_new_tags(["valid_one", "valid_two", "meme", "invalid-tag", "valid_three"], tag_file)
    assert "valid_one" in res
    assert "valid_two" in res
    assert "meme" not in res
    assert "invalid-tag" not in res
    # 達到上限 (MAX_KNOWN_TAGS = 150)
    assert len(res) == MAX_KNOWN_TAGS
    assert "valid_three" not in res
