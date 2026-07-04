"""
analyzer.pyが出力したスコアJSONから、GitHub Pagesで公開できる静的HTMLサイトを生成するスクリプト。

出力構成:
  output/site/assets/style.css                  … 共通CSS
  output/site/.nojekyll                          … GitHub PagesでのJekyll処理を無効化
  output/site/{grade}/{target_slug}/{hash}.html  … リリース単体のページ(アフィリエイト広告枠付き)
  output/site/{grade}/{target_slug}/index.html   … grade×target単位の一覧ページ
  output/site/index.html                         … 全体の一覧ページ

入力: output/scores/YYYY-MM-DD.json (analyzer.pyの出力)

各ページ下部には広告タグを差し込むための固定枠 <div id="affiliate-slot"> を設置している。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
from html import escape as h
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

SITE_TITLE = "調査リリース信頼性評価"

AFFILIATE_SLOT_HTML = """    <div id="affiliate-slot" class="affiliate-slot">
      <span class="affiliate-label">広告</span>
      <!-- ここにアフィリエイト広告タグを挿入してください -->
    </div>"""

CSS_CONTENT = """:root {
  --color-bg: #f7f7f5;
  --color-surface: #ffffff;
  --color-text: #1f2328;
  --color-muted: #57606a;
  --color-border: #e2e2e0;
  --color-a: #1a7f37;
  --color-b: #9a6700;
  --color-c: #cf222e;
  --color-unknown: #6e7781;
  --color-link: #0969da;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
  background: var(--color-bg);
  color: var(--color-text);
  line-height: 1.7;
}

.site-header {
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  padding: 16px 24px;
}

.site-title {
  font-weight: 700;
  font-size: 1.1rem;
  color: var(--color-text);
  text-decoration: none;
}

.container {
  max-width: 760px;
  margin: 0 auto;
  padding: 24px 20px 48px;
}

h1 { font-size: 1.5rem; margin-bottom: 0.5em; }
h2 { font-size: 1.15rem; margin-top: 1.8em; }

.release {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 24px;
}

.meta {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 1em 0;
  font-size: 0.92rem;
  color: var(--color-muted);
}

.meta dt { font-weight: 600; }
.meta dd { margin: 0; word-break: break-all; }

.grade-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 0.8rem;
  font-weight: 700;
  color: #fff;
  vertical-align: middle;
}
.grade-A { background: var(--color-a); }
.grade-B { background: var(--color-b); }
.grade-C { background: var(--color-c); }
.grade-unknown { background: var(--color-unknown); }

.score-breakdown {
  list-style: none;
  padding: 0;
  margin: 0.8em 0;
}
.score-breakdown li {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  border-bottom: 1px dashed var(--color-border);
  font-size: 0.92rem;
}

.flags {
  font-size: 0.9rem;
  color: var(--color-muted);
}

.release-list {
  list-style: none;
  padding: 0;
}
.release-item {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 10px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.release-item a {
  color: var(--color-link);
  text-decoration: none;
  font-weight: 600;
}
.release-item a:hover { text-decoration: underline; }
.meta-line {
  width: 100%;
  font-size: 0.82rem;
  color: var(--color-muted);
}

.affiliate-slot {
  margin-top: 32px;
  padding: 16px;
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  text-align: center;
  color: var(--color-muted);
  font-size: 0.85rem;
  min-height: 90px;
}
.affiliate-label {
  display: block;
  font-size: 0.7rem;
  letter-spacing: 0.05em;
  margin-bottom: 6px;
}

.site-footer {
  text-align: center;
  padding: 24px;
  color: var(--color-muted);
  font-size: 0.85rem;
}
.site-footer a { color: var(--color-link); }

.count { color: var(--color-muted); font-size: 0.9rem; }
"""


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


def embed_meta_json(meta: dict) -> str:
    """一覧再構築用のメタデータを非表示の<script>タグに埋め込む。

    <, >, & はJSONの構造文字としては使われないため、値の中に含まれる場合のみ
    \\uXXXX形式にエスケープする(json.loadsはこの形式を透過的に復元できる)。
    これにより </script> によるタグの意図しないクローズや、
    HTMLとして解釈されうる文字列の混入を防ぐ。
    """
    raw = json.dumps(meta, ensure_ascii=False)
    raw = (
        raw.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return f'<script type="application/json" id="release-meta">{raw}</script>'


def html_document(*, title: str, css_path: str, body: str) -> str:
    """HTML文書全体をレンダリングする。"""
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{h(title)}</title>
<link rel="stylesheet" href="{css_path}">
</head>
<body>
{body}
</body>
</html>
"""


def render_release_html(release: dict, *, home_path: str, css_path: str) -> str:
    """1件のリリースをHTMLページとしてレンダリングする。"""
    score = release.get("score", {})
    scores = score.get("scores", {})
    flags = score.get("flags", [])

    title = release.get("title", "")
    source = release.get("source", "")
    url = release.get("url", "")
    published_date = release.get("published_date", "")
    target = release.get("target", "")
    grade = grade_dir_name(score.get("grade"))
    total_score = score.get("total_score", "")
    one_line_summary = score.get("one_line_summary", "")
    reasoning = score.get("reasoning", "")

    meta_json = embed_meta_json(
        {
            "title": title,
            "source": source,
            "url": url,
            "published_date": published_date,
            "target": target,
            "grade": grade,
            "total_score": total_score,
            "one_line_summary": one_line_summary,
        }
    )

    score_items = "\n".join(
        f'      <li><span class="axis">{h(SCORE_AXIS_LABELS.get(key, key))}</span>'
        f'<span class="axis-score">{h(str(value))} / 20</span></li>'
        for key, value in scores.items()
    )
    flags_line = "、".join(flags) if flags else "なし"

    body = f"""<header class="site-header">
  <a class="site-title" href="{home_path}">{h(SITE_TITLE)}</a>
</header>
<main class="container">
  {meta_json}
  <article class="release">
    <h1>{h(title)}</h1>
    <dl class="meta">
      <dt>配信元</dt><dd>{h(source)}</dd>
      <dt>公開日</dt><dd>{h(published_date)}</dd>
      <dt>想定ターゲット</dt><dd>{h(target)}</dd>
      <dt>元記事</dt><dd><a href="{h(url)}" rel="nofollow noopener" target="_blank">{h(url)}</a></dd>
    </dl>

    <section class="score-summary">
      <h2>信頼性スコア: {h(str(total_score))} / 100
        <span class="grade-badge grade-{h(grade)}">grade {h(grade)}</span>
      </h2>
      <ul class="score-breakdown">
{score_items}
      </ul>
      <p class="flags"><strong>該当フラグ:</strong> {h(flags_line)}</p>
    </section>

    <section class="summary">
      <h2>一言コメント</h2>
      <p>{h(one_line_summary)}</p>
    </section>

    <section class="reasoning">
      <h2>評価理由</h2>
      <p>{h(reasoning)}</p>
    </section>
  </article>

{AFFILIATE_SLOT_HTML}
</main>
<footer class="site-footer">
  <p><a href="{home_path}">&laquo; 一覧に戻る</a></p>
</footer>"""

    return html_document(title=f"{title} | {SITE_TITLE}", css_path=css_path, body=body)


def render_index_html(
    *,
    page_title: str,
    entries: list[dict],
    home_path: str,
    css_path: str,
    is_top_level: bool,
) -> str:
    """一覧ページ(index.html)をレンダリングする。entriesは公開日降順を想定。"""
    items = "\n".join(
        f'''    <li class="release-item">
      <span class="grade-badge grade-{h(entry["grade"])}">grade {h(entry["grade"])}</span>
      <a href="{h(entry["relative_path"])}">{h(entry["title"])}</a>
      <span class="meta-line">{h(str(entry["total_score"]))}点 / {h(entry["source"])} / {h(entry["published_date"])}</span>
    </li>'''
        for entry in entries
    )

    if is_top_level:
        header_link = f'<span class="site-title">{h(SITE_TITLE)}</span>'
    else:
        header_link = f'<a class="site-title" href="{home_path}">{h(SITE_TITLE)}</a>'

    body = f"""<header class="site-header">
  {header_link}
</header>
<main class="container">
  <h1>{h(page_title)}</h1>
  <p class="count">件数: {len(entries)}</p>
  <ul class="release-list">
{items}
  </ul>

{AFFILIATE_SLOT_HTML}
</main>
<footer class="site-footer">
  <p>調査リリースの信頼性を客観的な開示情報に基づき評価しています。</p>
</footer>"""

    return html_document(title=page_title, css_path=css_path, body=body)


def write_assets(site_dir: Path) -> None:
    """共通CSSとGitHub Pages用の.nojekyllを書き出す。"""
    assets_dir = site_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "style.css").write_text(CSS_CONTENT, encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")


def write_release_pages(releases: list[dict], site_dir: Path) -> int:
    """スコア付きリリースをHTMLページとして書き出す。書き出した件数を返す。"""
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

        filename = f"{url_hash(release.get('url', ''))}.html"
        page_path = page_dir / filename
        html_text = render_release_html(
            release,
            home_path="../../index.html",
            css_path="../../assets/style.css",
        )
        page_path.write_text(html_text, encoding="utf-8")
        written += 1

    return written


_META_SCRIPT_PATTERN = re.compile(
    r'<script type="application/json" id="release-meta">(.*?)</script>', re.DOTALL
)


def scan_existing_pages(site_dir: Path) -> list[dict]:
    """site_dir配下の既存ページ(過去分含む)をスキャンし、一覧用エントリを収集する。"""
    entries: list[dict] = []
    if not site_dir.exists():
        return entries

    for grade_dir in sorted(p for p in site_dir.iterdir() if p.is_dir()):
        if grade_dir.name == "assets":
            continue
        for target_dir in sorted(p for p in grade_dir.iterdir() if p.is_dir()):
            for page_path in sorted(target_dir.glob("*.html")):
                if page_path.name == "index.html":
                    continue
                text = page_path.read_text(encoding="utf-8")
                match = _META_SCRIPT_PATTERN.search(text)
                if not match:
                    logger.warning("メタデータが見つかりません: %s", page_path)
                    continue
                raw_json = match.group(1)
                try:
                    meta = json.loads(raw_json)
                except json.JSONDecodeError:
                    logger.warning("メタデータのパースに失敗しました: %s", page_path)
                    continue
                entries.append(
                    {
                        "title": meta.get("title", ""),
                        "source": meta.get("source", ""),
                        "published_date": meta.get("published_date", ""),
                        "grade": grade_dir.name,
                        "target_slug": target_dir.name,
                        "total_score": meta.get("total_score", ""),
                        "filename": page_path.name,
                    }
                )
    return entries


def build_indexes(entries: list[dict], site_dir: Path) -> None:
    """スキャン済みエントリ全体から、grade×target単位と全体のindex.htmlを再構築する。"""
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
        index_path = site_dir / grade / target_slug / "index.html"
        index_title = f"grade {grade} / {target_slug} 一覧"
        index_path.write_text(
            render_index_html(
                page_title=index_title,
                entries=local_entries,
                home_path="../../index.html",
                css_path="../../assets/style.css",
                is_top_level=False,
            ),
            encoding="utf-8",
        )

    all_entries = sorted(entries, key=lambda e: e["published_date"], reverse=True)
    top_entries = [
        {**e, "relative_path": f"{e['grade']}/{e['target_slug']}/{e['filename']}"}
        for e in all_entries
    ]
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(
        render_index_html(
            page_title=SITE_TITLE,
            entries=top_entries,
            home_path="index.html",
            css_path="assets/style.css",
            is_top_level=True,
        ),
        encoding="utf-8",
    )


def build_site(releases: list[dict], site_dir: Path = SITE_DIR) -> None:
    """スコア付きリリースのリストから、当日分のページを追加し、サイト全体を再構築する。"""
    write_assets(site_dir)
    written = write_release_pages(releases, site_dir)

    # インデックスは過去分も含めてsite_dir全体を再スキャンして再構築する
    # (実行日ごとにindex.htmlが当日分だけで上書きされるのを防ぐため)
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
