from dataclasses import dataclass


@dataclass(frozen=True)
class Meme:
    image_url: str
    tags: tuple[str, ...]
    description: str
    source: str
    id: int | None = None
    created_at: str | None = None

