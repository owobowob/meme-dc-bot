import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from models.meme import Meme

GLOBAL_GUILD_ID = 0


class DatabaseError(RuntimeError):
    pass


class DuplicateMeme(DatabaseError):
    pass


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self):
        try:
            with closing(sqlite3.connect(self.path)) as conn, conn:
                conn.row_factory = sqlite3.Row
                yield conn
        except sqlite3.Error as exc:
            raise DatabaseError("無法操作梗圖資料庫。") from exc

    def initialize(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DatabaseError("無法建立梗圖資料庫目錄。") from exc
        with self.connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memes (
                    id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    image_url TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    description TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (guild_id, image_url)
                )
            """)

    def add(self, guild_id: int, meme: Meme) -> int:
        with self.connection() as conn:
            cursor = conn.execute("""
                INSERT INTO memes (guild_id, image_url, tags, description, source)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT (guild_id, image_url) DO NOTHING
            """, (guild_id, meme.image_url, json.dumps(meme.tags), meme.description, meme.source))
            if not cursor.rowcount:
                raise DuplicateMeme("這個圖片網址已存在。")
            return cursor.lastrowid

    @staticmethod
    def from_row(row: sqlite3.Row) -> Meme:
        return Meme(row["image_url"], tuple(json.loads(row["tags"])),
                    row["description"], row["source"], row["id"], row["created_at"])

    def get(self, guild_id: int, meme_id: int) -> Meme | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM memes WHERE guild_id = ? AND id = ?",
                               (guild_id, meme_id)).fetchone()
            return self.from_row(row) if row else None

    def contains_url(self, image_url: str) -> bool:
        with self.connection() as conn:
            return conn.execute(
                "SELECT 1 FROM memes WHERE image_url = ? LIMIT 1", (image_url,),
            ).fetchone() is not None

    def update_tags(self, meme_id: int, tags: tuple[str, ...]) -> bool:
        with self.connection() as conn:
            return bool(conn.execute(
                "UPDATE memes SET tags = ? WHERE id = ?",
                (json.dumps(tags), meme_id)
            ).rowcount)

    def append_tags(self, guild_id: int, meme_id: int, tags: tuple[str, ...]) -> tuple[str, ...] | None:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT tags FROM memes WHERE id = ? AND guild_id IN (?, ?)",
                (meme_id, guild_id, GLOBAL_GUILD_ID),
            ).fetchone()
            if row is None:
                return None
            merged = tuple(dict.fromkeys((*json.loads(row["tags"]), *tags)))
            conn.execute("UPDATE memes SET tags = ? WHERE id = ?", (json.dumps(merged), meme_id))
            return merged

    def delete(self, *args) -> bool:
        """刪除梗圖資料。

        支援兩種簽章：
        - delete(meme_id: int) -> bool (依據 meme_id 刪除)
        - delete(guild_id: int, meme_id: int) -> bool (依據 guild_id 與 meme_id 刪除)
        """
        if len(args) == 1:
            meme_id = args[0]
            with self.connection() as conn:
                return bool(conn.execute("DELETE FROM memes WHERE id = ?", (meme_id,)).rowcount)
        elif len(args) == 2:
            guild_id, meme_id = args
            with self.connection() as conn:
                return bool(conn.execute(
                    "DELETE FROM memes WHERE guild_id = ? AND id = ?",
                    (guild_id, meme_id)
                ).rowcount)
        else:
            raise TypeError("delete() 接受 1 個或 2 個參數 (meme_id 或 guild_id, meme_id)")

    def search(self, guild_id: int, tags: list[str]) -> list[Meme]:
        with self.connection() as conn:
            rows = conn.execute("""
                SELECT memes.*, (
                    SELECT COUNT(DISTINCT value) FROM json_each(memes.tags)
                    WHERE value IN (SELECT value FROM json_each(?))
                ) AS score
                FROM memes WHERE guild_id IN (?, 0) AND score > 0
                ORDER BY score DESC, guild_id DESC, id DESC
            """, (json.dumps(tags), guild_id))
            return [self.from_row(row) for row in rows]
