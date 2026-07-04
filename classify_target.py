"""
fetch_rss.pyが抽出したリリースを「消費者向け」か「企業向け」かに簡易分類するスクリプト。

キーワードベースの判定を行う:
  - 「担当者」「BtoB」「企業の」等の企業向けキーワードを含む場合 → 企業向け
  - それ以外 → 消費者向け

入力: output/releases/YYYY-MM-DD.json (fetch_rss.pyの出力)
出力: output/classified/YYYY-MM-DD.json (targetフィールドを追加)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("classify_target")

# 企業向け(BtoB)と判定するキーワード。いずれかを含めば企業向けとする。
B2B_KEYWORDS: list[str] = [
    "担当者",
    "BtoB",
    "B to B",
    "企業の",
    "法人",
    "経営者",
    "決裁者",
    "人事担当",
    "マーケティング担当",
    "IT担当",
    "中小企業",
    "事業者向け",
]

TARGET_CONSUMER = "消費者向け"
TARGET_BUSINESS = "企業向け"

BASE_DIR = Path(__file__).resolve().parent
RELEASES_DIR = BASE_DIR / "output" / "releases"
CLASSIFIED_DIR = BASE_DIR / "output" / "classified"


def classify_target(title: str, body_text: str) -> str:
    """タイトルと本文から消費者向け/企業向けを判定する。"""
    combined = f"{title} {body_text}"
    if any(keyword in combined for keyword in B2B_KEYWORDS):
        return TARGET_BUSINESS
    return TARGET_CONSUMER


def classify_releases(releases: list[dict]) -> list[dict]:
    """リリースのリストに target フィールドを付与する。"""
    classified: list[dict] = []
    for release in releases:
        title = release.get("title", "")
        body_text = release.get("body_text", "")
        target = classify_target(title, body_text)
        classified.append({**release, "target": target})
    return classified


def find_input_path(date_str: str | None) -> Path:
    """入力JSONファイルのパスを決定する。指定がなければ最新のファイルを使う。"""
    if date_str:
        path = RELEASES_DIR / f"{date_str}.json"
        if not path.exists():
            raise FileNotFoundError(f"指定日のファイルが見つかりません: {path}")
        return path

    candidates = sorted(RELEASES_DIR.glob("*.json"))
    if not candidates:
        raise FileNotFoundError(f"入力ファイルが見つかりません: {RELEASES_DIR}")
    return candidates[-1]


def main() -> None:
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    input_path = find_input_path(date_arg)

    logger.info("読み込み中: %s", input_path)
    releases = json.loads(input_path.read_text(encoding="utf-8"))

    classified = classify_releases(releases)

    consumer_count = sum(1 for r in classified if r["target"] == TARGET_CONSUMER)
    business_count = sum(1 for r in classified if r["target"] == TARGET_BUSINESS)
    logger.info("分類結果: 消費者向け %d件 / 企業向け %d件", consumer_count, business_count)

    CLASSIFIED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CLASSIFIED_DIR / input_path.name
    output_path.write_text(
        json.dumps(classified, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("保存しました: %s", output_path)


if __name__ == "__main__":
    main()
