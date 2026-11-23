"""從命令列測試兩個梗圖標籤。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.analyzer import DEFAULT_PROVIDER, TagExtractionError, extract_meme_tags

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="從聊天文字抽出 2 個梗圖標籤（JSON）。")
    parser.add_argument("text", nargs="?", help="一句聊天內容。省略則進入互動輸入。")
    parser.add_argument(
        "--provider",
        choices=("gemini", "openai"),
        default=DEFAULT_PROVIDER if DEFAULT_PROVIDER in {"gemini", "openai"} else "gemini",
        help="LLM 供應商，預設讀 LLM_PROVIDER，否則 gemini。",
    )
    parser.add_argument("--model", default=None, help="覆寫模型名稱。")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    text = args.text or input("請輸入一句聊天內容： ").strip()

    try:
        payload = extract_meme_tags(text, provider=args.provider, model=args.model)
    except TagExtractionError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

