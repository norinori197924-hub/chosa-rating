"""
PRTIMESと@PressのRSSフィードから「調査・アンケート」カテゴリのリリースを収集するスクリプト。

タイトルまたは本文に指定キーワードを含むリリースのみを抽出し、
/output/releases/YYYY-MM-DD.json に保存する。

RSSフィードはローリング形式(直近の一定件数のみを返す)のため、同じ記事が
複数日にわたってフィードに残り続けることがある。output/site配下の既存記事
(generate_site.scan_existing_pages)から採点済みURLの集合を取得し、
既に採点済みのURLは抽出対象から除外することで、同一記事の重複採点を防ぐ。
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

import generate_site as gs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("fetch_rss")

# RSSフィード一覧: (source_name, feed_url)
# PRTIMES/@Pressともに「調査・アンケート」専用のRSSは提供されていないため、
# 全件フィードを取得しキーワードでフィルタする。
RSS_FEEDS: list[tuple[str, str]] = [
    ("PRTIMES", "https://prtimes.jp/index.rdf"),
    ("@Press", "https://www.atpress.ne.jp/rss/index.rdf"),
]

# タイトル/本文に含まれていれば抽出対象とするキーワード
TARGET_KEYWORDS: list[str] = [
    "意識調査",
    "実態調査",
    "利用実態",
    "アンケート調査",
    "消費者調査",
    "購買行動",
    "に関する調査",
]

OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "releases"
REQUEST_TIMEOUT = 15
REQUEST_INTERVAL_SEC = 1.0
USER_AGENT = "chosa-rating-bot/1.0 (+https://github.com/)"


@dataclass
class Release:
    title: str
    url: str
    source: str
    published_date: str
    body_text: str


def strip_html(raw_html: str) -> str:
    """HTMLタグを除去してプレーンテキストを返す。"""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def contains_target_keyword(*texts: str) -> bool:
    """いずれかのテキストにターゲットキーワードが含まれるか判定する。"""
    combined = " ".join(texts)
    return any(keyword in combined for keyword in TARGET_KEYWORDS)


def normalize_published_date(raw_date: str) -> str:
    """RSSの日付文字列をISO 8601形式に正規化する。パース失敗時は空文字を返す。"""
    if not raw_date:
        return ""
    try:
        dt = parsedate_to_datetime(raw_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        return dt.isoformat()
    except ValueError:
        logger.warning("日付のパースに失敗しました: %s", raw_date)
        return ""


def fetch_feed(url: str) -> bytes | None:
    """RSSフィードを取得する。失敗時はNoneを返す。"""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        logger.error("フィード取得に失敗しました: %s (%s)", url, exc)
        return None


# RSS 2.0 / RDF (RSS 1.0) の両方で使われる要素名のタグ(namespace省略形で一致させる)
_ITEM_TAGS = {"item"}
_FIELD_TAGS = {
    "title": {"title"},
    "link": {"link"},
    "description": {"description", "encoded"},
    "date": {"pubDate", "date"},
}


def _localname(tag: str) -> str:
    """namespace付きタグから要素名のみを取り出す。"""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_feed_items(raw_xml: bytes) -> list[dict[str, str]]:
    """RSS/RDFのXMLをパースし、item要素のフィールド辞書のリストを返す。"""
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError as exc:
        logger.error("XMLのパースに失敗しました: %s", exc)
        return []

    items: list[dict[str, str]] = []
    for elem in root.iter():
        if _localname(elem.tag) not in _ITEM_TAGS:
            continue
        fields: dict[str, str] = {"title": "", "link": "", "description": "", "date": ""}
        for child in elem:
            name = _localname(child.tag)
            text = (child.text or "").strip()
            for field_key, tag_names in _FIELD_TAGS.items():
                if name in tag_names and text:
                    fields[field_key] = text
        items.append(fields)
    return items


def collect_releases_from_feed(source: str, url: str) -> list[Release]:
    """1つのRSSフィードから、キーワード条件に合致するリリースを抽出する。"""
    logger.info("RSS取得中: %s (%s)", source, url)
    raw_xml = fetch_feed(url)
    if raw_xml is None:
        return []

    items = parse_feed_items(raw_xml)
    logger.info("%s: %d件のitemを取得", source, len(items))

    releases: list[Release] = []
    for item in items:
        title = strip_html(item.get("title", ""))
        body_text = strip_html(item.get("description", ""))
        link = item.get("link", "").strip()

        if not title or not link:
            continue
        if not contains_target_keyword(title, body_text):
            continue

        releases.append(
            Release(
                title=title,
                url=link,
                source=source,
                published_date=normalize_published_date(item.get("date", "")),
                body_text=body_text,
            )
        )

    logger.info("%s: %d件が抽出条件に合致", source, len(releases))
    return releases


def load_processed_urls() -> set[str]:
    """output/site配下の既存記事から、採点済み(=既に記事ページが存在する)URLの集合を取得する。"""
    entries = gs.scan_existing_pages(gs.SITE_DIR)
    return {entry["url"] for entry in entries if entry.get("url")}


def save_releases(releases: list[Release], output_dir: Path = OUTPUT_DIR) -> Path:
    """抽出結果を日付付きJSONファイルとして保存する。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    output_path = output_dir / f"{today}.json"

    payload = [asdict(release) for release in releases]
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("保存しました: %s (%d件)", output_path, len(payload))
    return output_path


def main() -> None:
    processed_urls = load_processed_urls()
    logger.info("採点済みURL: %d件を除外対象として読み込みました", len(processed_urls))

    all_releases: list[Release] = []
    for index, (source, url) in enumerate(RSS_FEEDS):
        all_releases.extend(collect_releases_from_feed(source, url))
        if index < len(RSS_FEEDS) - 1:
            time.sleep(REQUEST_INTERVAL_SEC)

    before_count = len(all_releases)
    all_releases = [r for r in all_releases if r.url not in processed_urls]
    skipped = before_count - len(all_releases)
    if skipped:
        logger.info("採点済みのため除外しました: %d件", skipped)

    save_releases(all_releases)
    logger.info("合計 %d件のリリースを抽出しました", len(all_releases))


if __name__ == "__main__":
    main()
