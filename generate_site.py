"""
analyzer.pyが出力したスコアJSONから、静的サイト用のMarkdownページを生成するスクリプト。

出力構成:
  output/site/{grade}/{target_slug}/{hash}.md   … リリース単体のページ(YAML Front Matter付き)
  output/site/{grade}/{target_slug}/index.md    … grade×target単位の一覧ページ
  output/site/index.md                          … 全体の一覧ページ

入力: output/scores/YYYY-MM-DD.json (analyzer.pyの出力)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("generate_site")

BASE_DIR = Path(__file__).resolve().parent
SCORES_DIR = BASE_DIR / "output" / "scores"
SITE_DIR = BASE_DIR / "output" / "site"

VALID_GRADES = {"A", "B", "C"}
GRADE_UNKNOWN = "unknown"

TARGET_SLUGS = {
    "消費者向け": "consumer",
    "企業向け": "business",
}
TARGET_UNKNOWN = "unknown"

SCORE_AXIS_LABELS = {
    "transparency": "調査主体の透明性",
    "methodology": "調査手法の開示",
    "sample_validity": "サンプルの妥当性",
    "conflict_of_interest": "利益相反",
    "neutrality": "質問文・見出しの中立性",
}


def grade_dir_name(grade: str | None) -> str:
    """gradeからディレクトリ名を決定する。未知の値は unknown にフォールバックする。"""
    if grade in VALID_GRADES:
        return grade
    return GRADE_UNKNOWN


def target_slug_name(target: str | None) -> str:
    """targetからディレクトリ名(スラッグ)を決定する。"""
    return TARGET_SLUGS.get(target or "", TARGET_UNKNOWN)


def url_hash(url: str) -> str:
    """URLから安定したファイル名用ハッシュを生成する。"""
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:12]


def yaml_escape(value: str) -> str:
    """YAML Front Matter用にダブルクォート文字列をエスケープする。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def yaml_list(values: list[str]) -> str:
    """文字列リストをYAMLのフロー形式配列に変換する。"""
    if not values:
        return "[]"
    return "[" + ", ".join(f'"{yaml_escape(v)}"' for v in values) + "]"


def render_release_markdown(release: dict) -> str:
    """1件のリリースをYAML Front Matter付きMarkdownとしてレンダリングする。"""
    score = release.get("score", {})
    scores = score.get("scores", {})
    flags = score.get("flags", [])

    title = release.get("title", "")
    source = release.get("source", "")
    url = release.get("url", "")
    published_date = release.get("published_date", "")
    target = release.get("target", "")
    grade = score.get("grade", "")
    total_score = score.get("total_score", "")
    one_line_summary = score.get("one_line_summary", "")
    reasoning = score.get("reasoning", "")

    front_matter = "\n".join(
        [
            "---",
            f'title: "{yaml_escape(title)}"',
            f'source: "{yaml_escape(source)}"',
            f'url: "{yaml_escape(url)}"',
            f'published_date: "{yaml_escape(published_date)}"',
            f'target: "{yaml_escape(target)}"',
            f'grade: "{yaml_escape(str(grade))}"',
            f"total_score: {total_score if isinstance(total_score, (int, float)) else 'null'}",
            f'one_line_summary: "{yaml_escape(one_line_summary)}"',
            f"flags: {yaml_list(flags)}",
            "---",
        ]
    )

    score_lines = "\n".join(
        f"- {SCORE_AXIS_LABELS.get(key, key)}: {value} / 20"
        for key, value in scores.items()
    )
    flags_line = "、".join(flags) if flags else "なし"

    body = f"""
# {title}

- 配信元: {source}
- 公開日: {published_date}
- 想定ターゲット: {target}
- 元記事: [{url}]({url})

## 信頼性スコア: {total_score} / 100 (grade {grade})

{score_lines}

**該当フラグ**: {flags_line}

## 一言コメント

{one_line_summary}

## 評価理由

{reasoning}
"""
    return front_matter + "\n" + body.strip() + "\n"


def render_index_markdown(title: str, entries: list[dict]) -> str:
    """一覧ページ(index.md)をレンダリングする。entriesは公開日降順を想定。"""
    lines = [f"# {title}", "", f"件数: {len(entries)}", ""]
    for entry in entries:
        lines.append(
            f"- [{entry['title']}]({entry['relative_path']}) "
            f"— {entry['grade']}評価 ({entry['total_score']}点) "
            f"/ {entry['source']} / {entry['published_date']}"
        )
    lines.append("")
    return "\n".join(lines)


def write_release_pages(releases: list[dict], site_dir: Path) -> int:
    """スコア付きリリースをMarkdownページとして書き出す。書き出した件数を返す。"""
    written = 0
    for release in releases:
        score = release.get("score", {})
        if "error" in score or "total_score" not in score:
            logger.warning(
                "スコアリング未完了のためスキップします: %s", release.get("url", "")
            )
            continue

        grade = grade_dir_name(score.get("grade"))
        target_slug = target_slug_name(release.get("target"))
        page_dir = site_dir / grade / target_slug
        page_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{url_hash(release.get('url', ''))}.md"
        page_path = page_dir / filename
        page_path.write_text(render_release_markdown(release), encoding="utf-8")
        written += 1

    return written


_FRONT_MATTER_PATTERN = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def parse_front_matter(markdown_text: str) -> dict[str, str]:
    """generate_siteが出力したYAML Front Matterを簡易パースする(このスクリプト専用)。"""
    match = _FRONT_MATTER_PATTERN.match(markdown_text)
    if not match:
        return {}
    data: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        data[key] = value
    return data


def scan_existing_pages(site_dir: Path) -> list[dict]:
    """site_dir配下の既存ページ(過去分含む)をスキャンし、一覧用エントリを収集する。"""
    entries: list[dict] = []
    if not site_dir.exists():
        return entries

    for grade_dir in sorted(p for p in site_dir.iterdir() if p.is_dir()):
        for target_dir in sorted(p for p in grade_dir.iterdir() if p.is_dir()):
            for page_path in sorted(target_dir.glob("*.md")):
                if page_path.name == "index.md":
                    continue
                front = parse_front_matter(page_path.read_text(encoding="utf-8"))
                total_score_raw = front.get("total_score", "")
                try:
                    total_score: object = int(total_score_raw)
                except (TypeError, ValueError):
                    total_score = total_score_raw
                entries.append(
                    {
                        "title": front.get("title", ""),
                        "source": front.get("source", ""),
                        "published_date": front.get("published_date", ""),
                        "grade": grade_dir.name,
                        "target_slug": target_dir.name,
                        "total_score": total_score,
                        "filename": page_path.name,
                    }
                )
    return entries


def build_indexes(entries: list[dict], site_dir: Path) -> None:
    """スキャン済みエントリ全体から、grade×target単位と全体のindex.mdを再構築する。"""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        grouped.setdefault((entry["grade"], entry["target_slug"]), []).append(entry)

    for (grade, target_slug), group_entries in grouped.items():
        group_entries = sorted(
            group_entries, key=lambda e: e["published_date"], reverse=True
        )
        local_entries = [
            {**e, "relative_path": e["filename"]} for e in group_entries
        ]
        index_path = site_dir / grade / target_slug / "index.md"
        index_title = f"grade {grade} / {target_slug} 一覧"
        index_path.write_text(
            render_index_markdown(index_title, local_entries), encoding="utf-8"
        )

    all_entries = sorted(entries, key=lambda e: e["published_date"], reverse=True)
    top_entries = [
        {**e, "relative_path": f"{e['grade']}/{e['target_slug']}/{e['filename']}"}
        for e in all_entries
    ]
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.md").write_text(
        render_index_markdown("調査リリース信頼性評価 一覧", top_entries),
        encoding="utf-8",
    )


def build_site(releases: list[dict], site_dir: Path = SITE_DIR) -> None:
    """スコア付きリリースのリストから、当日分のページを追加し、サイト全体のインデックスを再構築する。"""
    written = write_release_pages(releases, site_dir)

    # インデックスは過去分も含めてsite_dir全体を再スキャンして再構築する
    # (実行日ごとにindex.mdが当日分だけで上書きされるのを防ぐため)
    all_entries = scan_existing_pages(site_dir)
    build_indexes(all_entries, site_dir)

    logger.info(
        "サイト生成完了: 当日分 %d件を書き出し、全体で %d件のページを一覧に反映 -> %s",
        written,
        len(all_entries),
        site_dir,
    )


def find_input_path(date_str: str | None) -> Path:
    """入力JSONファイルのパスを決定する。指定がなければ最新のファイルを使う。"""
    if date_str:
        path = SCORES_DIR / f"{date_str}.json"
        if not path.exists():
            raise FileNotFoundError(f"指定日のファイルが見つかりません: {path}")
        return path

    candidates = sorted(SCORES_DIR.glob("*.json"))
    if not candidates:
        raise FileNotFoundError(f"入力ファイルが見つかりません: {SCORES_DIR}")
    return candidates[-1]


def main() -> None:
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    input_path = find_input_path(date_arg)

    logger.info("読み込み中: %s", input_path)
    releases = json.loads(input_path.read_text(encoding="utf-8"))

    build_site(releases)


if __name__ == "__main__":
    main()
