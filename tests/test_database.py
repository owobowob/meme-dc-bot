import pytest

from models.meme import Meme
from services.database import GLOBAL_GUILD_ID, Database, DuplicateMeme


@pytest.fixture
def database(tmp_path):
    database = Database(tmp_path / "data" / "memes.db")
    database.initialize()
    return database


def test_crud_and_ranking(database):
    both = Meme("https://i.imgur.com/a.gif", ("doge", "stonks"), "", "https://i.imgur.com/a.gif")
    one = Meme("https://i.imgur.com/b.gif", ("doge",), "", "https://i.imgur.com/b.gif")
    first = database.add(1, both)
    second = database.add(1, one)
    assert database.get(1, first).created_at
    assert [meme.id for meme in database.search(1, ["doge", "stonks"])] == [first, second]
    assert not database.search(2, ["doge"])
    assert not database.delete(2, first)
    with pytest.raises(DuplicateMeme):
        database.add(1, both)
    assert database.delete(1, first)
    assert database.get(1, first) is None


def test_global_guild_sharing_and_ranking(database):
    # 全域圖庫 (guild_id = 0)
    global_meme = Meme("https://i.imgur.com/global.gif", ("doge", "stonks"), "Global", "https://i.imgur.com/global.gif")
    # 伺服器 10 專屬圖庫
    guild_meme = Meme("https://i.imgur.com/local.gif", ("doge", "stonks"), "Local", "https://i.imgur.com/local.gif")
    # 伺服器 10 的單標籤圖庫
    partial_meme = Meme("https://i.imgur.com/part.gif", ("doge",), "Partial", "https://i.imgur.com/part.gif")

    gid_global = database.add(GLOBAL_GUILD_ID, global_meme)
    gid_local = database.add(10, guild_meme)
    gid_partial = database.add(10, partial_meme)

    # 1. 在伺服器 10 查詢，應該能搜到伺服器 10 與全域 0
    # 排序規則：score DESC, guild_id DESC, id DESC
    # guild_meme (score 2, guild_id 10) vs global_meme (score 2, guild_id 0) -> guild_meme 優先
    # partial_meme (score 1, guild_id 10) -> 排最後
    results_g10 = database.search(10, ["doge", "stonks"])
    ids_g10 = [m.id for m in results_g10]
    assert ids_g10 == [gid_local, gid_global, gid_partial]

    # 2. 在伺服器 20 查詢，專屬資料庫無資料，但能命中全域圖庫
    results_g20 = database.search(20, ["doge", "stonks"])
    ids_g20 = [m.id for m in results_g20]
    assert ids_g20 == [gid_global]

    # 3. 驗證隔離性：伺服器 10 無法以 guild_id 隔離限制 get 或 delete 全域圖庫
    assert database.get(10, gid_global) is None
    assert not database.delete(10, gid_global)
    # 全域本身可透過 guild_id = 0 存取與刪除
    assert database.get(GLOBAL_GUILD_ID, gid_global) is not None


def test_update_tags_and_direct_delete(database):
    meme = Meme("https://i.imgur.com/update.gif", ("doge",), "Test", "https://i.imgur.com/update.gif")
    meme_id = database.add(GLOBAL_GUILD_ID, meme)

    # 正常更新標籤
    assert database.update_tags(meme_id, ("doge", "to_the_moon")) is True
    updated = database.get(GLOBAL_GUILD_ID, meme_id)
    assert updated.tags == ("doge", "to_the_moon")

    # 更新不存在的 ID
    assert database.update_tags(999999, ("new_tag",)) is False

    # 依 meme_id 單一參數直接刪除
    assert database.delete(meme_id) is True
    assert database.get(GLOBAL_GUILD_ID, meme_id) is None

    # 刪除已不存在的 ID
    assert database.delete(meme_id) is False
    assert database.delete(999999) is False
