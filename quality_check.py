"""
output/site 配下の全記事ページ(release-metaのJSON)を走査し、データ品質を機械的にチェックするスクリプト。

チェック内容:
  1. reasoningが空文字、または10文字未満の記事
  2. scores内の5軸(transparency, methodology, sample_validity,
     conflict_of_interest, neutrality)のいずれかが欠けている、または0〜20の範囲外の記事
  3. total_scoreと5軸の合計が一致しない記事
  4. 同一urlを持つ記事の重複

結果はGitHub ActionsのJob Summary($GITHUB_STEP_SUMMARY)にMarkdown形式で出力する。
同じ出力に、今回のバッチの運用サマリー(新規採点数・grade別累計・APIコスト概算)も追加する。

このチェックで問題が見つかってもワークフロー自体は失敗させない(exit codeは常に0)。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import generate_site as gs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("quality_check")

BASE_DIR = Path(__file__).resolve().parent
SCORES_DIR = BASE_DIR / "output" / "scores"
USAGE_DIR = BASE_DIR / "output" / "usage"
SITE_DIR = gs.SITE_DIR

REQUIRED_AXES = [
    "transparency",
    "methodology",
    "sample_validity",
    "conflict_of_interest",
    "neutrality",
]

# Batch API(標準料金の50%割引)適用後のHaiku 4.5料金(1Mトークンあたり)。
# 標準料金は入力$1.00/出力$5.00。将来的にAnthropicの料金改定があれば要更新。
BATCH_INPUT_PRICE_PER_MTOK = 0.50
BATCH_OUTPUT_PRICE_PER_MTOK = 2.50


def find_reasoning_issues(entry: dict) -> list[str]:
    """reasoningが空文字・極端に短い場合の問題を検出する。"""
    reasoning = (entry.get("reasoning") or "").strip()
    if len(reasoning) < 10:
        return [f"reasoningが空文字または10文字未満です(長さ: {len(reasoning)})"]
    return []


def find_score_issues(entry: dict) -> list[str]:
    """scoresの欠落・範囲外・total_scoreとの不一致を検出する。"""
    issues: list[str] = []
    scores = entry.get("scores") or {}

    missing = [axis for axis in REQUIRED_AXES if axis not in scores]
    if missing:
        issues.append(f"scoresに以下の軸が欠けています: {', '.join(missing)}")

    out_of_range = []
    for axis in REQUIRED_AXES:
        if axis not in scores:
            continue
        value = scores[axis]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not (0 <= value <= 20):
            out_of_range.append(f"{axis}={value!r}")
    if out_of_range:
        issues.append(f"scoresが0〜20の範囲外、または数値ではありません: {', '.join(out_of_range)}")

    if not missing and not out_of_range:
        total_score = entry.get("total_score")
        axis_sum = sum(scores[axis] for axis in REQUIRED_AXES)
        if isinstance(total_score, (int, float)) and axis_sum != total_score:
            issues.append(f"total_score({total_score})と5軸合計({axis_sum})が一致しません")

    return issues


def find_duplicate_url_issues(entries: list[dict]) -> list[dict]:
    """同一urlを持つ記事の重複を検出する。"""
    url_to_paths: dict[str, list[str]] = {}
    for entry in entries:
        url = entry.get("url", "")
        if url:
            url_to_paths.setdefault(url, []).append(entry.get("relative_path", ""))

    findings: list[dict] = []
    for url, paths in url_to_paths.items():
        if len(paths) <= 1:
            continue
        for path in paths:
            others = ", ".join(p for p in paths if p != path)
            findings.append(
                {"path": path, "issue": f"同一URLの重複記事があります(他: {others}): {url}"}
            )
    return findings


def check_all(entries: list[dict]) -> list[dict]:
    """全記事に対して品質チェックを実行し、問題一覧を返す。"""
    findings: list[dict] = []
    for entry in entries:
        path = entry.get("relative_path", "")
        for issue in find_reasoning_issues(entry) + find_score_issues(entry):
            findings.append({"path": path, "issue": issue})

    findings.extend(find_duplicate_url_issues(entries))
    return findings


def load_latest_usage() -> dict | None:
    """output/usage配下の最新のトークン使用量レコードを読み込む。"""
    if not USAGE_DIR.exists():
        return None
    candidates = sorted(USAGE_DIR.glob("*.json"))
    if not candidates:
        return None
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


def count_newly_scored() -> int:
    """output/scores配下の最新ファイルから、今回のバッチで新規採点した記事数を数える。"""
    if not SCORES_DIR.exists():
        return 0
    candidates = sorted(SCORES_DIR.glob("*.json"))
    if not candidates:
        return 0
    releases = json.loads(candidates[-1].read_text(encoding="utf-8"))
    return sum(1 for r in releases if "error" not in (r.get("score") or {}))


def render_summary_markdown(findings: list[dict], entries: list[dict]) -> str:
    """品質チェック結果と運用サマリーをMarkdownとして組み立てる。"""
    lines = ["## 品質チェック結果", ""]

    if not findings:
        lines.append("✅ 品質チェック: 問題ありません")
    else:
        lines.append(f"⚠️ 品質チェックで {len(findings)} 件の問題が見つかりました")
        lines.append("")
        lines.append("| ファイル | 問題内容 |")
        lines.append("|---|---|")
        for finding in findings:
            lines.append(f"| `{finding['path']}` | {finding['issue']} |")

    lines.append("")
    lines.append("## 運用サマリー")
    lines.append("")

    newly_scored = count_newly_scored()
    grade_counts: dict[str, int] = {}
    for entry in entries:
        grade = entry.get("grade", "unknown")
        grade_counts[grade] = grade_counts.get(grade, 0) + 1

    lines.append(f"- 今回のバッチで新規に採点した記事数: {newly_scored}件")
    grade_breakdown = " / ".join(
        f"{g}={grade_counts.get(g, 0)}件" for g in ("A", "B", "C")
    )
    if grade_counts.get("unknown"):
        grade_breakdown += f" / unknown={grade_counts['unknown']}件"
    lines.append(f"- grade別内訳(累計、全{len(entries)}件): {grade_breakdown}")

    usage = load_latest_usage()
    if usage:
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cost = (
            input_tokens / 1_000_000 * BATCH_INPUT_PRICE_PER_MTOK
            + output_tokens / 1_000_000 * BATCH_OUTPUT_PRICE_PER_MTOK
        )
        lines.append(
            f"- 今回のバッチのAPI呼び出しコスト概算: 約${cost:.4f}"
            f"(入力{input_tokens:,}トークン、出力{output_tokens:,}トークン、"
            f"{usage.get('model', 'claude-haiku-4-5')} Batch API料金換算)"
        )
    else:
        lines.append("- 今回のバッチのAPI呼び出しコスト概算: トークン使用量の記録(output/usage)が見つからないため算出できませんでした")

    return "\n".join(lines)


def main() -> None:
    entries = gs.scan_existing_pages(SITE_DIR)
    findings = check_all(entries)

    for finding in findings:
        logger.warning("品質チェック: %s - %s", finding["path"], finding["issue"])

    summary_md = render_summary_markdown(findings, entries)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(summary_md + "\n")
    else:
        print(summary_md)

    logger.info("品質チェック完了: %d件の問題", len(findings))
    sys.exit(0)


if __name__ == "__main__":
    main()
