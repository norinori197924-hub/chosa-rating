"""
analyzer.pyが出力したスコアJSONから、GitHub Pagesで公開できる静的HTMLサイトを生成するスクリプト。

デザインは「案1: ネイビー×信頼感(コーポレート/報道機関風)」を採用:
  - ネイビー×白ベース、gradeバッジ(A=緑、B=黄、C=赤)
  - セリフの見出し+ゴシック本文、マストヘッド+カード型レイアウト
  - ライト/ダークモード両対応(prefers-color-scheme と data-theme 属性トグルの両方に追従)

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

# 記事詳細ページの内訳表示に使う正式名称
SCORE_AXIS_LABELS = {
    "transparency": "調査主体の透明性",
    "methodology": "調査手法の開示",
    "sample_validity": "サンプルの妥当性",
    "conflict_of_interest": "利益相反",
    "neutrality": "質問文・見出しの中立性",
}

# 一覧カードの内訳表示に使う短縮名称
SCORE_AXIS_SHORT_LABELS = {
    "transparency": "透明性",
    "methodology": "手法開示",
    "sample_validity": "サンプル",
    "conflict_of_interest": "利益相反",
    "neutrality": "中立性",
}

SITE_TITLE = "調査リリース信頼性評価"
SITE_TAGLINE = "SURVEY RELEASE TRUST INDEX"

AFFILIATE_SLOT_HTML = """  <div id="affiliate-slot" class="affiliate-slot">
    <span class="affiliate-label">SPONSORED</span>
    <!-- ここにアフィリエイト広告タグを挿入してください -->
  </div>"""

CSS_CONTENT = """:root {
  --navy-900: #0b1f3a;
  --navy-800: #122b4d;
  --navy-700: #1c3f6e;
  --paper: #f3f6fa;
  --surface: #ffffff;
  --ink: #16202c;
  --muted: #5b6b7d;
  --hairline: #d8dee6;
  --gold: #9c752f;
  --grade-a: #1f7a4d;
  --grade-b: #a8720f;
  --grade-c: #b3302c;
  --grade-a-soft: #e4f3ea;
  --grade-b-soft: #f6ecd8;
  --grade-c-soft: #f8e2e0;
}

@media (prefers-color-scheme: dark) {
  :root {
    --navy-900: #060e1c;
    --navy-800: #0d1a2e;
    --navy-700: #16294a;
    --paper: #0c1420;
    --surface: #131e2c;
    --ink: #e7ecf2;
    --muted: #94a3b5;
    --hairline: #26364a;
    --gold: #d1a55c;
    --grade-a: #49b784;
    --grade-b: #d9a13e;
    --grade-c: #e0685c;
    --grade-a-soft: #123527;
    --grade-b-soft: #3a2c12;
    --grade-c-soft: #3a1815;
  }
}
:root[data-theme="dark"] {
  --navy-900: #060e1c;
  --navy-800: #0d1a2e;
  --navy-700: #16294a;
  --paper: #0c1420;
  --surface: #131e2c;
  --ink: #e7ecf2;
  --muted: #94a3b5;
  --hairline: #26364a;
  --gold: #d1a55c;
  --grade-a: #49b784;
  --grade-b: #d9a13e;
  --grade-c: #e0685c;
  --grade-a-soft: #123527;
  --grade-b-soft: #3a2c12;
  --grade-c-soft: #3a1815;
}
:root[data-theme="light"] {
  --navy-900: #0b1f3a;
  --navy-800: #122b4d;
  --navy-700: #1c3f6e;
  --paper: #f3f6fa;
  --surface: #ffffff;
  --ink: #16202c;
  --muted: #5b6b7d;
  --hairline: #d8dee6;
  --gold: #9c752f;
  --grade-a: #1f7a4d;
  --grade-b: #a8720f;
  --grade-c: #b3302c;
  --grade-a-soft: #e4f3ea;
  --grade-b-soft: #f6ecd8;
  --grade-c-soft: #f8e2e0;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
  line-height: 1.7;
}

.masthead {
  background: var(--navy-900);
  color: #fff;
  padding: 28px 24px 22px;
  border-bottom: 3px solid var(--gold);
}
.masthead-inner {
  max-width: 880px;
  margin: 0 auto;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
}
.brand {
  font-family: Georgia, "Hiragino Mincho ProN", "Yu Mincho", serif;
  font-size: 1.9rem;
  letter-spacing: 0.01em;
  margin: 0;
}
a.brand { text-decoration: none; color: inherit; }
.tagline {
  font-size: 0.82rem;
  color: #b9c6d6;
  letter-spacing: 0.08em;
}

.summary-bar {
  max-width: 880px;
  margin: 0 auto;
  padding: 18px 24px 0;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
}
.stat {
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: 4px;
  padding: 14px 16px;
}
.stat .num {
  font-family: Georgia, serif;
  font-size: 1.6rem;
  font-variant-numeric: tabular-nums;
  color: var(--navy-700);
}
@media (prefers-color-scheme: dark) {
  .stat .num { color: var(--gold); }
}
:root[data-theme="dark"] .stat .num { color: var(--gold); }
.stat .label { font-size: 0.75rem; color: var(--muted); letter-spacing: 0.04em; }

main {
  max-width: 880px;
  margin: 0 auto;
  padding: 24px 24px 56px;
}

h2.section-title {
  font-family: Georgia, "Hiragino Mincho ProN", "Yu Mincho", serif;
  font-size: 1.05rem;
  color: var(--navy-700);
  border-bottom: 1px solid var(--hairline);
  padding-bottom: 8px;
  margin: 0 0 16px;
  letter-spacing: 0.02em;
}
@media (prefers-color-scheme: dark) {
  h2.section-title { color: #cfe0f2; }
}
:root[data-theme="dark"] h2.section-title { color: #cfe0f2; }

.empty-note { color: var(--muted); font-size: 0.9rem; }

.release-card {
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-left: 4px solid var(--navy-700);
  border-radius: 3px;
  padding: 18px 20px;
  margin-bottom: 14px;
  display: grid;
  grid-template-columns: 88px 1fr auto;
  gap: 18px;
  align-items: start;
}
.release-card[data-grade="A"] { border-left-color: var(--grade-a); }
.release-card[data-grade="B"] { border-left-color: var(--grade-b); }
.release-card[data-grade="C"] { border-left-color: var(--grade-c); }

.grade-pill {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 10px 4px;
  border-radius: 4px;
  font-weight: 700;
}
.grade-pill .letter { font-family: Georgia, serif; font-size: 1.5rem; line-height: 1; }
.grade-pill .word { font-size: 0.62rem; letter-spacing: 0.06em; margin-top: 2px; }
[data-grade="A"] .grade-pill { background: var(--grade-a-soft); color: var(--grade-a); }
[data-grade="B"] .grade-pill { background: var(--grade-b-soft); color: var(--grade-b); }
[data-grade="C"] .grade-pill { background: var(--grade-c-soft); color: var(--grade-c); }
[data-grade="unknown"] .grade-pill { background: var(--hairline); color: var(--muted); }

.release-body h3 {
  margin: 0 0 6px;
  font-size: 1.05rem;
  line-height: 1.5;
}
.release-body h3 a { color: var(--ink); text-decoration: none; }
.release-body h3 a:hover { text-decoration: underline; }

.release-meta {
  font-size: 0.78rem;
  color: var(--muted);
  margin-bottom: 8px;
}
.release-meta .sep { margin: 0 6px; }

.axis-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  font-size: 0.74rem;
  color: var(--muted);
  margin: 8px 0 6px;
  border-top: 1px dashed var(--hairline);
  padding-top: 8px;
}
.axis-row span b { color: var(--ink); font-variant-numeric: tabular-nums; }
.axis-row.full { font-size: 0.86rem; gap: 8px 20px; border-top: none; padding-top: 0; margin: 0 0 16px; }

.summary-line {
  font-size: 0.88rem;
  font-style: italic;
  color: var(--navy-700);
}
@media (prefers-color-scheme: dark) {
  .summary-line { color: #cfe0f2; }
}
:root[data-theme="dark"] .summary-line { color: #cfe0f2; }

.score-block {
  text-align: right;
  min-width: 84px;
}
.score-block .value {
  font-family: Georgia, serif;
  font-size: 1.7rem;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.score-block .of100 { font-size: 0.72rem; color: var(--muted); }

.affiliate-slot {
  margin-top: 32px;
  padding: 16px;
  border: 1px dashed var(--hairline);
  border-radius: 4px;
  text-align: center;
  color: var(--muted);
  font-size: 0.82rem;
}
.affiliate-label {
  display: block;
  font-size: 0.65rem;
  letter-spacing: 0.1em;
  color: var(--gold);
  margin-bottom: 4px;
}

footer {
  text-align: center;
  padding: 20px;
  font-size: 0.78rem;
  color: var(--muted);
  border-top: 1px solid var(--hairline);
}

/* 記事詳細ページ */
.detail-card {
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: 4px;
  padding: 28px 28px 24px;
}
.detail-head {
  display: flex;
  gap: 20px;
  align-items: flex-start;
  border-bottom: 1px solid var(--hairline);
  padding-bottom: 20px;
  margin-bottom: 20px;
}
.detail-head .grade-pill { flex: 0 0 auto; padding: 14px 10px; }
.detail-head .grade-pill .letter { font-size: 1.9rem; }
.detail-title-block { flex: 1 1 auto; min-width: 0; }
.detail-title {
  font-family: Georgia, "Hiragino Mincho ProN", "Yu Mincho", serif;
  font-size: 1.5rem;
  line-height: 1.5;
  margin: 0 0 8px;
}
.detail-head .score-block { flex: 0 0 auto; }
.detail-head .score-block .value { font-size: 2.2rem; }

.source-link {
  font-size: 0.8rem;
  color: var(--muted);
  word-break: break-all;
  margin-top: 4px;
}
.source-link a { color: var(--navy-700); }
@media (prefers-color-scheme: dark) { .source-link a { color: var(--gold); } }
:root[data-theme="dark"] .source-link a { color: var(--gold); }

.detail-card .section-title { margin-top: 24px; border-bottom: none; padding-bottom: 0; }

.flags-line { font-size: 0.82rem; color: var(--muted); margin-top: 6px; }
.flags-line b { color: var(--ink); }

.back-link { display: inline-block; margin-top: 20px; font-size: 0.85rem; }
.back-link a { color: var(--navy-700); text-decoration: none; }
@media (prefers-color-scheme: dark) { .back-link a { color: var(--gold); } }
:root[data-theme="dark"] .back-link a { color: var(--gold); }
.back-link a:hover { text-decoration: underline; }

@media (max-width: 560px) {
  .release-card { grid-template-columns: 64px 1fr; }
  .score-block { grid-column: 1 / -1; text-align: left; display: flex; align-items: baseline; gap: 8px; }
  .summary-bar { grid-template-columns: 1fr; }
  .detail-head { flex-direction: column; }
  .detail-head .score-block { text-align: left; }
}
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
    """1件のリリースを記事詳細ページとしてレンダリングする。"""
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
            "scores": scores,
        }
    )

    axis_items = "\n".join(
        f'      <span>{h(SCORE_AXIS_LABELS.get(key, key))} <b>{h(str(value))}</b>/20</span>'
        for key, value in scores.items()
    )
    flags_line = "、".join(flags) if flags else "なし"

    body = f"""<div class="masthead">
  <div class="masthead-inner">
    <a class="brand" href="{home_path}">{h(SITE_TITLE)}</a>
    <span class="tagline">{h(SITE_TAGLINE)}</span>
  </div>
</div>
<main>
  {meta_json}
  <article class="detail-card">
    <div class="detail-head">
      <div class="grade-pill"><span class="letter">{h(grade)}</span><span class="word">GRADE</span></div>
      <div class="detail-title-block">
        <h1 class="detail-title">{h(title)}</h1>
        <div class="release-meta">{h(source)}<span class="sep">/</span>{h(published_date)}<span class="sep">/</span>{h(target)}</div>
        <div class="source-link">元記事: <a href="{h(url)}" rel="nofollow noopener" target="_blank">{h(url)}</a></div>
      </div>
      <div class="score-block">
        <div class="value">{h(str(total_score))}</div>
        <div class="of100">/ 100点</div>
      </div>
    </div>

    <div class="axis-row full">
{axis_items}
    </div>
    <p class="summary-line">「{h(one_line_summary)}」</p>
    <p class="flags-line"><b>該当フラグ:</b> {h(flags_line)}</p>

    <h2 class="section-title">評価理由</h2>
    <p>{h(reasoning)}</p>
  </article>

{AFFILIATE_SLOT_HTML}

  <p class="back-link"><a href="{home_path}">&laquo; 一覧に戻る</a></p>
</main>
<footer>
  調査リリースの信頼性を客観的な開示情報に基づき評価しています。
</footer>"""

    return html_document(title=f"{title} | {SITE_TITLE}", css_path=css_path, body=body)


def render_index_card(entry: dict) -> str:
    """一覧ページ内の1リリース分のカードをレンダリングする。"""
    grade = entry.get("grade", GRADE_UNKNOWN)
    scores = entry.get("scores") or {}
    axis_spans = " ".join(
        f'<span>{h(SCORE_AXIS_SHORT_LABELS.get(key, key))} <b>{h(str(value))}</b>/20</span>'
        for key, value in scores.items()
    )
    axis_row = f'      <div class="axis-row">{axis_spans}</div>\n' if axis_spans else ""

    one_line_summary = entry.get("one_line_summary", "")
    summary_line = (
        f'      <p class="summary-line">「{h(one_line_summary)}」</p>\n' if one_line_summary else ""
    )

    return f"""  <article class="release-card" data-grade="{h(grade)}">
    <div class="grade-pill"><span class="letter">{h(grade)}</span><span class="word">GRADE</span></div>
    <div class="release-body">
      <h3><a href="{h(entry.get('relative_path', ''))}">{h(entry.get('title', ''))}</a></h3>
      <div class="release-meta">{h(entry.get('source', ''))}<span class="sep">/</span>{h(entry.get('published_date', ''))}<span class="sep">/</span>{h(entry.get('target', ''))}</div>
{axis_row}{summary_line}    </div>
    <div class="score-block">
      <div class="value">{h(str(entry.get('total_score', '')))}</div>
      <div class="of100">/ 100点</div>
    </div>
  </article>"""


def render_index_html(
    *,
    page_title: str,
    entries: list[dict],
    home_path: str,
    css_path: str,
    is_top_level: bool,
) -> str:
    """一覧ページ(index.html)をレンダリングする。entriesは公開日降順を想定。"""
    numeric_scores = [e["total_score"] for e in entries if isinstance(e.get("total_score"), (int, float))]
    avg_score = round(sum(numeric_scores) / len(numeric_scores)) if numeric_scores else 0

    stats: list[tuple[int, str]] = [(len(entries), "評価件数"), (avg_score, "平均スコア")]
    if is_top_level:
        grade_a_count = sum(1 for e in entries if e.get("grade") == "A")
        stats.append((grade_a_count, "grade A 件数"))

    stat_tiles = "\n".join(
        f'    <div class="stat"><div class="num">{h(str(num))}</div><div class="label">{h(label)}</div></div>'
        for num, label in stats
    )

    cards = "\n".join(render_index_card(entry) for entry in entries)
    if not cards:
        cards = '  <p class="empty-note">対象のリリースはまだありません。</p>'

    if is_top_level:
        brand_html = f'<h1 class="brand">{h(SITE_TITLE)}</h1>'
    else:
        brand_html = f'<a class="brand" href="{home_path}">{h(SITE_TITLE)}</a>'

    body = f"""<div class="masthead">
  <div class="masthead-inner">
    {brand_html}
    <span class="tagline">{h(SITE_TAGLINE)}</span>
  </div>
</div>

<div class="summary-bar">
{stat_tiles}
</div>

<main>
  <h2 class="section-title">{h(page_title)}</h2>

{cards}

{AFFILIATE_SLOT_HTML}
</main>

<footer>
  調査リリースの信頼性を客観的な開示情報に基づき評価しています。
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
                        "target": meta.get("target", ""),
                        "grade": grade_dir.name,
                        "target_slug": target_dir.name,
                        "total_score": meta.get("total_score", ""),
                        "one_line_summary": meta.get("one_line_summary", ""),
                        "scores": meta.get("scores", {}),
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
