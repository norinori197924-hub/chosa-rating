"""
analyzer.pyが出力したスコアJSONから、GitHub Pagesで公開できる静的HTMLサイトを生成するスクリプト。

デザインは「案1: ネイビー×信頼感(コーポレート/報道機関風)」を採用:
  - ネイビー×白ベース、gradeバッジ(A=緑、B=黄、C=赤)
  - セリフの見出し+ゴシック本文、マストヘッド+カード型レイアウト
  - ライト/ダークモード両対応(prefers-color-scheme と data-theme 属性トグルの両方に追従)
  - CSSのみによる控えめな背景パターン(ヘッダー斜線 / 本文ドットグリッド)
  - 全体一覧ページにはキーワード検索・ジャンル/対象/評価/期間フィルタ(クライアントサイドJS)
  - 一覧・詳細ページには5軸スコアのバー表示とgrade凡例を追加

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

# 軸ごとの簡易アイコン(バー表示の視認性向上用)
AXIS_ICONS = {
    "transparency": "🔍",
    "methodology": "📋",
    "sample_validity": "👥",
    "conflict_of_interest": "⚖️",
    "neutrality": "🎯",
}

# classify_target.pyがgenreを付与していない旧データ向けのフォールバック値
GENRE_UNKNOWN = "その他"

# gradeの意味を示す凡例(一覧ページに表示)
GRADE_LEGEND = [
    ("A", "優良", "開示水準が高く、利益相反や誘導的表現の懸念が少ない"),
    ("B", "要注意", "一部の開示情報が不足しており、独自の検証が難しい"),
    ("C", "参考程度", "開示水準が低い、または利益相反・誘導的表現の懸念がある"),
]

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
  background-color: var(--paper);
  /* 控えめなドットグリッドの背景パターン(テーマの--hairline色に追従するため両モードで破綻しない) */
  background-image: radial-gradient(circle at 1px 1px, var(--hairline) 1px, transparent 0);
  background-size: 26px 26px;
  background-attachment: fixed;
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
  line-height: 1.7;
}

.masthead {
  position: relative;
  background-color: var(--navy-900);
  /* 斜線パターン+半透明の地色を重ねて、白文字の可読性を保ったまま控えめな装飾にする */
  background-image: repeating-linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.05) 0px,
    rgba(255, 255, 255, 0.05) 1px,
    transparent 1px,
    transparent 14px
  );
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

/* gradeの意味を示す凡例 */
.grade-legend {
  max-width: 880px;
  margin: 14px auto 0;
  padding: 0 24px;
  display: flex;
  flex-wrap: wrap;
  gap: 10px 20px;
}
.grade-legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.78rem;
  color: var(--muted);
}
.grade-legend-item b { color: var(--ink); }
.grade-legend-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 4px;
  font-family: Georgia, serif;
  font-weight: 700;
  font-size: 0.85rem;
}
.grade-legend-badge[data-grade="A"] { background: var(--grade-a-soft); color: var(--grade-a); }
.grade-legend-badge[data-grade="B"] { background: var(--grade-b-soft); color: var(--grade-b); }
.grade-legend-badge[data-grade="C"] { background: var(--grade-c-soft); color: var(--grade-c); }

/* 検索・フィルタツールバー(全体一覧ページのみ) */
.toolbar {
  max-width: 880px;
  margin: 18px auto 0;
  padding: 0 24px;
}
.search-bar input[type="search"] {
  width: 100%;
  padding: 10px 14px;
  font-size: 0.95rem;
  border: 1px solid var(--hairline);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink);
}
.search-bar input[type="search"]:focus {
  outline: 2px solid var(--navy-700);
  outline-offset: 1px;
}
.filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.filter-bar select {
  padding: 7px 10px;
  font-size: 0.82rem;
  border: 1px solid var(--hairline);
  border-radius: 4px;
  background: var(--surface);
  color: var(--ink);
}
.result-count {
  margin: 10px 0 0;
  font-size: 0.78rem;
  color: var(--muted);
}
.no-results { display: none; color: var(--muted); font-size: 0.9rem; padding: 12px 0; }
.no-results.is-visible { display: block; }

/* ジャンルタグ */
.genre-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--hairline);
  color: var(--muted);
  font-size: 0.72rem;
}

/* 5軸スコアのバー表示 */
.axis-bars {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin: 8px 0 6px;
}
.axis-bar-row {
  display: grid;
  grid-template-columns: 6.5em 1fr 2.4em;
  align-items: center;
  gap: 8px;
  font-size: 0.72rem;
  color: var(--muted);
}
.axis-bar-track {
  height: 5px;
  border-radius: 3px;
  background: var(--hairline);
  overflow: hidden;
}
.axis-bar-fill {
  display: block;
  height: 100%;
  background: var(--navy-700);
  border-radius: 3px;
}
@media (prefers-color-scheme: dark) { .axis-bar-fill { background: var(--gold); } }
:root[data-theme="dark"] .axis-bar-fill { background: var(--gold); }
.axis-bar-value { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink); }

.axis-bars-full {
  margin: 0 0 16px;
  gap: 7px;
}
.axis-bars-full .axis-bar-row {
  grid-template-columns: 11em 1fr 3em;
  font-size: 0.84rem;
}
.axis-bars-full .axis-bar-track { height: 7px; }

@media (max-width: 560px) {
  .release-card { grid-template-columns: 64px 1fr; }
  .score-block { grid-column: 1 / -1; text-align: left; display: flex; align-items: baseline; gap: 8px; }
  .summary-bar { grid-template-columns: 1fr; }
  .detail-head { flex-direction: column; }
  .detail-head .score-block { text-align: left; }
  .axis-bar-row, .axis-bars-full .axis-bar-row { grid-template-columns: 5.5em 1fr 2em; }
}
"""

SEARCH_JS_CONTENT = """(function () {
  "use strict";
  var dataEl = document.getElementById("site-data");
  if (!dataEl) return;

  var entries;
  try {
    entries = JSON.parse(dataEl.textContent);
  } catch (err) {
    return;
  }

  var searchInput = document.getElementById("site-search");
  var genreSelect = document.getElementById("filter-genre");
  var targetSelect = document.getElementById("filter-target");
  var gradeSelect = document.getElementById("filter-grade");
  var periodSelect = document.getElementById("filter-period");
  var resultCount = document.getElementById("result-count");
  var noResults = document.getElementById("no-results");
  if (!searchInput || !genreSelect || !targetSelect || !gradeSelect || !periodSelect) return;

  var cardById = {};
  document.querySelectorAll(".release-card[data-id]").forEach(function (card) {
    cardById[card.getAttribute("data-id")] = card;
  });

  function normalize(value) {
    return (value || "").toString().toLowerCase();
  }

  function matchesEntry(entry, query) {
    if (query) {
      var haystack = normalize(entry.title) + " " + normalize(entry.body_text);
      if (haystack.indexOf(query) === -1) return false;
    }
    if (genreSelect.value && entry.genre !== genreSelect.value) return false;
    if (targetSelect.value && entry.target !== targetSelect.value) return false;
    if (gradeSelect.value && entry.grade !== gradeSelect.value) return false;
    if (periodSelect.value) {
      var days = parseInt(periodSelect.value, 10);
      var published = new Date(entry.date);
      if (isNaN(published.getTime())) return false;
      var cutoffMs = Date.now() - days * 24 * 60 * 60 * 1000;
      if (published.getTime() < cutoffMs) return false;
    }
    return true;
  }

  function applyFilters() {
    var query = normalize(searchInput.value.trim());
    var visible = 0;
    entries.forEach(function (entry) {
      var card = cardById[entry.id];
      if (!card) return;
      var ok = matchesEntry(entry, query);
      card.style.display = ok ? "" : "none";
      if (ok) visible += 1;
    });
    if (resultCount) {
      resultCount.textContent = visible + " 件表示中(全 " + entries.length + " 件)";
    }
    if (noResults) {
      noResults.classList.toggle("is-visible", visible === 0);
    }
  }

  [searchInput, genreSelect, targetSelect, gradeSelect, periodSelect].forEach(function (el) {
    el.addEventListener("input", applyFilters);
    el.addEventListener("change", applyFilters);
  });

  applyFilters();
})();
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


def embed_json_script(data, script_id: str) -> str:
    """任意のデータを非表示の<script>タグに埋め込む。

    <, >, & はJSONの構造文字としては使われないため、値の中に含まれる場合のみ
    \\uXXXX形式にエスケープする(json.loadsはこの形式を透過的に復元できる)。
    これにより </script> によるタグの意図しないクローズや、
    HTMLとして解釈されうる文字列の混入を防ぐ。
    """
    raw = json.dumps(data, ensure_ascii=False)
    raw = (
        raw.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    return f'<script type="application/json" id="{script_id}">{raw}</script>'


def embed_meta_json(meta: dict) -> str:
    """一覧再構築用のリリースメタデータを非表示の<script>タグに埋め込む。"""
    return embed_json_script(meta, "release-meta")


def render_axis_bars(scores: dict, *, labels: dict, variant: str) -> str:
    """5軸スコアをバー visualization としてレンダリングする(variant: 'compact' | 'full')。"""
    if not scores:
        return ""
    rows = []
    for key, value in scores.items():
        try:
            pct = max(0, min(100, round(float(value) / 20 * 100)))
        except (TypeError, ValueError):
            pct = 0
        label = labels.get(key, key)
        icon = AXIS_ICONS.get(key, "")
        rows.append(
            f'    <div class="axis-bar-row">'
            f'<span class="axis-bar-label">{icon} {h(label)}</span>'
            f'<span class="axis-bar-track"><span class="axis-bar-fill" style="width:{pct}%"></span></span>'
            f'<span class="axis-bar-value">{h(str(value))}</span>'
            f"</div>"
        )
    rows_html = "\n".join(rows)
    return f'<div class="axis-bars axis-bars-{variant}">\n{rows_html}\n    </div>'


def render_grade_legend() -> str:
    """gradeの意味を示す凡例をレンダリングする。"""
    items = "\n".join(
        f'    <div class="grade-legend-item"><span class="grade-legend-badge" data-grade="{h(letter)}">{h(letter)}</span><b>{h(label)}</b><span>{h(desc)}</span></div>'
        for letter, label, desc in GRADE_LEGEND
    )
    return f"""<div class="grade-legend">
{items}
  </div>"""


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
    genre = release.get("genre") or GENRE_UNKNOWN
    grade = grade_dir_name(score.get("grade"))
    total_score = score.get("total_score", "")
    one_line_summary = score.get("one_line_summary", "")
    reasoning = score.get("reasoning", "")
    body_text = release.get("body_text", "")

    meta_json = embed_meta_json(
        {
            "title": title,
            "source": source,
            "url": url,
            "published_date": published_date,
            "target": target,
            "genre": genre,
            "grade": grade,
            "total_score": total_score,
            "one_line_summary": one_line_summary,
            "scores": scores,
            "body_text": body_text[:400],
        }
    )

    axis_bars = render_axis_bars(scores, labels=SCORE_AXIS_LABELS, variant="full")
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
        <div class="release-meta">{h(source)}<span class="sep">/</span>{h(published_date)}<span class="sep">/</span>{h(target)}<span class="sep">/</span><span class="genre-tag">{h(genre)}</span></div>
        <div class="source-link">元記事: <a href="{h(url)}" rel="nofollow noopener" target="_blank">{h(url)}</a></div>
      </div>
      <div class="score-block">
        <div class="value">{h(str(total_score))}</div>
        <div class="of100">/ 100点</div>
      </div>
    </div>

    {axis_bars}
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
    genre = entry.get("genre") or GENRE_UNKNOWN
    scores = entry.get("scores") or {}
    axis_bars = render_axis_bars(scores, labels=SCORE_AXIS_SHORT_LABELS, variant="compact")

    one_line_summary = entry.get("one_line_summary", "")
    summary_line = (
        f'      <p class="summary-line">「{h(one_line_summary)}」</p>\n' if one_line_summary else ""
    )

    # filenameはURLハッシュ由来のため、同一記事が別日に別grade/targetで再スコアされると衝突しうる。
    # relative_pathはgrade/target_slugを含むため一覧ページ内で一意になる。
    entry_id = entry.get("relative_path") or (entry.get("filename") or "").rsplit(".", 1)[0]

    return f"""  <article class="release-card" data-grade="{h(grade)}" data-id="{h(entry_id)}">
    <div class="grade-pill"><span class="letter">{h(grade)}</span><span class="word">GRADE</span></div>
    <div class="release-body">
      <h3><a href="{h(entry.get('relative_path', ''))}">{h(entry.get('title', ''))}</a></h3>
      <div class="release-meta">{h(entry.get('source', ''))}<span class="sep">/</span>{h(entry.get('published_date', ''))}<span class="sep">/</span>{h(entry.get('target', ''))}<span class="sep">/</span><span class="genre-tag">{h(genre)}</span></div>
      {axis_bars}
{summary_line}    </div>
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

    grade_legend = render_grade_legend()

    toolbar_html = ""
    search_script_tag = ""
    if is_top_level:
        distinct_genres = sorted({(e.get("genre") or GENRE_UNKNOWN) for e in entries})
        genre_options = "\n".join(
            f'      <option value="{h(g)}">{h(g)}</option>' for g in distinct_genres
        )
        search_payload = [
            {
                "id": e.get("relative_path") or (e.get("filename") or "").rsplit(".", 1)[0],
                "title": e.get("title", ""),
                "body_text": (e.get("body_text") or "")[:400],
                "genre": e.get("genre") or GENRE_UNKNOWN,
                "target": e.get("target", ""),
                "grade": e.get("grade", GRADE_UNKNOWN),
                "date": e.get("published_date", ""),
            }
            for e in entries
        ]
        site_data_json = embed_json_script(search_payload, "site-data")

        toolbar_html = f"""<div class="toolbar">
  <div class="search-bar">
    <input type="search" id="site-search" placeholder="キーワードで検索(タイトル・本文)" aria-label="キーワード検索">
  </div>
  <div class="filter-bar">
    <select id="filter-genre" aria-label="ジャンルで絞り込み">
      <option value="">ジャンル: すべて</option>
{genre_options}
    </select>
    <select id="filter-target" aria-label="対象で絞り込み">
      <option value="">対象: すべて</option>
      <option value="消費者向け">消費者向け</option>
      <option value="企業向け">企業向け</option>
    </select>
    <select id="filter-grade" aria-label="評価で絞り込み">
      <option value="">評価: すべて</option>
      <option value="A">grade A</option>
      <option value="B">grade B</option>
      <option value="C">grade C</option>
    </select>
    <select id="filter-period" aria-label="期間で絞り込み">
      <option value="">期間: すべて</option>
      <option value="7">直近1週間</option>
      <option value="30">直近1ヶ月</option>
    </select>
  </div>
  <p id="result-count" class="result-count"></p>
</div>
{site_data_json}"""
        search_script_tag = '<script src="assets/search.js" defer></script>'

    body = f"""<div class="masthead">
  <div class="masthead-inner">
    {brand_html}
    <span class="tagline">{h(SITE_TAGLINE)}</span>
  </div>
</div>

<div class="summary-bar">
{stat_tiles}
</div>

{grade_legend}

{toolbar_html}

<main>
  <h2 class="section-title">{h(page_title)}</h2>
  <p id="no-results" class="no-results">条件に一致するリリースが見つかりませんでした。</p>

{cards}

{AFFILIATE_SLOT_HTML}
</main>

<footer>
  調査リリースの信頼性を客観的な開示情報に基づき評価しています。
</footer>
{search_script_tag}"""

    return html_document(title=page_title, css_path=css_path, body=body)


def write_assets(site_dir: Path) -> None:
    """共通CSS・検索フィルタ用JS・GitHub Pages用の.nojekyllを書き出す。"""
    assets_dir = site_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "style.css").write_text(CSS_CONTENT, encoding="utf-8")
    (assets_dir / "search.js").write_text(SEARCH_JS_CONTENT, encoding="utf-8")
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
                        "genre": meta.get("genre") or GENRE_UNKNOWN,
                        "grade": grade_dir.name,
                        "target_slug": target_dir.name,
                        "total_score": meta.get("total_score", ""),
                        "one_line_summary": meta.get("one_line_summary", ""),
                        "scores": meta.get("scores", {}),
                        "body_text": meta.get("body_text", ""),
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
