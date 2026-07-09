"""
既存記事のgradeをtotal_scoreから再計算して補正し(フェーズ1)、
survey_overviewが未抽出の記事にAPIで抽出を行う(フェーズ2)スクリプト。

フェーズ1(grade再計算、API呼び出しなし):
  output/site 配下の全記事のtotal_scoreから calculate_grade() で正しいgradeを再計算し、
  現在のgradeと異なる場合は該当gradeフォルダへ記事を移動し、catchy_titleを再構築する。
  (total_score自体は再計算しない。5軸の採点をやり直すわけではなく、
   既に確定しているtotal_scoreに対して正しいgradeラベルを付け直すだけ)

フェーズ2(survey_overview抽出、API呼び出しあり、--apply時のみ):
  survey_overviewフィールドが存在しない記事について、本文をClaude APIに再度投げて
  survey_overviewのみ抽出する(スコア・gradeは再計算しない、抽出専用の軽量プロンプト)。

デフォルトはdry-runで、フェーズ1の変更プレビュー(タイトル: 旧grade→新grade)と、
適用後のgrade別件数サマリーを表示するのみ。実際に書き換えるには --apply を指定する。
--skip-overview を指定すると、--apply時でもフェーズ2(survey_overview抽出)をスキップする。

使い方:
  python backfill_grade.py                         # dry-run、フェーズ1のプレビューのみ
  python backfill_grade.py --apply                  # フェーズ1+2を実行
  python backfill_grade.py --apply --skip-overview   # フェーズ1のみ実行
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import anthropic

import analyzer
import generate_site as gs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill_grade")

SITE_DIR = gs.SITE_DIR


def safe_calculate_grade(total_score) -> str:
    """analyzer.calculate_grade()を、total_scoreの型が不正な場合の安全策を挟んで呼び出す。
    しきい値の定義自体はanalyzer.py側(calculate_grade)を単一の情報源として参照する。"""
    try:
        return analyzer.calculate_grade(int(total_score))
    except (TypeError, ValueError):
        return "C"


def extract_title_suffix(entry: dict) -> str:
    """既存のcatchy_titleから、末尾に付与されていた動詞句(title_suffix)を復元する。
    catchy_titleが「【grade評価・点数】raw_title」パターンに一致しない場合は空文字を返す。"""
    raw_title = entry.get("raw_title") or entry.get("title", "")
    catchy_title = entry.get("catchy_title")
    if not catchy_title:
        return ""
    grade = entry.get("grade", "")
    total_score = entry.get("total_score", "")
    prefix = f"【{grade}評価・{total_score}点】{raw_title}"
    if catchy_title.startswith(prefix):
        return catchy_title[len(prefix):]
    return ""


def build_corrected_entries(entries: list[dict]) -> list[dict]:
    """各entryのtotal_scoreからgradeを再計算し、必要ならgrade・relative_path・
    catchy_title・titleを更新したentryのリストを返す(引数のリスト自体は変更しない)。"""
    corrected: list[dict] = []
    for entry in entries:
        new_grade = safe_calculate_grade(entry.get("total_score"))
        old_grade = entry.get("grade")
        if new_grade == old_grade:
            corrected.append(entry)
            continue

        new_entry = dict(entry)
        new_entry["grade"] = new_grade
        new_entry["relative_path"] = f"{new_grade}/{entry['target_slug']}/{entry['filename']}"

        raw_title = entry.get("raw_title") or entry.get("title", "")
        suffix = extract_title_suffix(entry)
        new_catchy_title = analyzer.build_catchy_title(
            raw_title,
            {
                "grade": new_grade,
                "total_score": entry.get("total_score"),
                "title_suffix": suffix,
            },
        )
        new_entry["catchy_title"] = new_catchy_title
        new_entry["title"] = new_catchy_title
        corrected.append(new_entry)

    return corrected


def summarize_grade_distribution(entries: list[dict]) -> dict[str, int]:
    """grade別の件数を集計する。"""
    counts: dict[str, int] = {"A": 0, "B": 0, "C": 0}
    for entry in entries:
        grade = entry.get("grade", "")
        counts[grade] = counts.get(grade, 0) + 1
    return counts


def apply_grade_corrections(
    entries: list[dict], corrected_entries: list[dict], site_dir: Path
) -> None:
    """フェーズ1の変更を実際にファイルへ適用する。

    gradeが変わった記事は旧gradeフォルダのファイルを削除したうえで、全記事を再レンダリングする
    (関連リンク・grade別一覧の整合性を保つため、grade変更の影響を受けない記事も含めて全件書き直す)。
    """
    for old_entry, new_entry in zip(entries, corrected_entries):
        if old_entry.get("grade") == new_entry.get("grade"):
            continue
        old_path = site_dir / old_entry["relative_path"]
        if old_path.exists():
            old_path.unlink()
            logger.info(
                "grade変更のため旧ページを削除しました: %s (新: %s)",
                old_entry["relative_path"],
                new_entry["relative_path"],
            )

    for entry in corrected_entries:
        gs.write_release_page(entry, corrected_entries, site_dir)

    gs.build_indexes(corrected_entries, site_dir)


# --- フェーズ2: survey_overview抽出 ---

SURVEY_OVERVIEW_SYSTEM_PROMPT = """あなたは市場調査のプレスリリースから客観的な事実情報を抽出する専門家です。
以下の4項目を本文から抽出してください。記載がなければ推測せず「記載なし」としてください(捏造禁止):
- target_respondents: 調査対象者
- question_count: 設問数
- sample_size: サンプル数(有効回答数)
- quota_allocation: 割付(性別・年代等の均等割付の有無)

【出力形式】
以下のJSON形式のみで出力してください。前置き・後書きは一切不要です。

{
  "target_respondents": "調査対象者(記載なしの場合は「記載なし」)",
  "question_count": "設問数(記載なしの場合は「記載なし」)",
  "sample_size": "サンプル数・有効回答数(記載なしの場合は「記載なし」)",
  "quota_allocation": "割付の有無・内容(記載なしの場合は「記載なし」)"
}"""

SURVEY_OVERVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "target_respondents": {"type": "string"},
        "question_count": {"type": "string"},
        "sample_size": {"type": "string"},
        "quota_allocation": {"type": "string"},
    },
    "required": [
        "target_respondents",
        "question_count",
        "sample_size",
        "quota_allocation",
    ],
    "additionalProperties": False,
}


def needs_survey_overview(entry: dict) -> bool:
    """survey_overviewが未抽出(空・欠落)かどうかを判定する。"""
    return not entry.get("survey_overview")


def extract_survey_overview(client: anthropic.Anthropic, entry: dict) -> dict | None:
    """本文(body_text)からsurvey_overviewのみをAPIで抽出する。

    output/site上のbody_textは検索用に400文字へ切り詰められたものしか保持していないため、
    元記事より抽出精度が落ちる可能性がある点に留意すること。
    """
    title = entry.get("raw_title") or entry.get("title", "")
    body_text = entry.get("body_text", "")
    user_content = f"タイトル: {title}\n\n本文:\n{body_text}"

    response = client.messages.create(
        model=analyzer.MODEL,
        max_tokens=512,
        system=SURVEY_OVERVIEW_SYSTEM_PROMPT,
        output_config={
            "format": {"type": "json_schema", "schema": SURVEY_OVERVIEW_SCHEMA}
        },
        messages=[{"role": "user", "content": user_content}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error(
            "survey_overviewのJSONパースに失敗しました: %s", entry.get("relative_path")
        )
        return None


def apply_survey_overview_extraction(entries: list[dict], site_dir: Path) -> list[dict]:
    """survey_overviewが未抽出の記事についてAPIで抽出し、ページを更新する。更新後のentriesを返す。"""
    targets = [e for e in entries if needs_survey_overview(e)]
    if not targets:
        logger.info("survey_overview抽出が必要な記事はありません")
        return entries

    logger.info("survey_overview抽出対象: %d件", len(targets))
    client = anthropic.Anthropic()
    updated_entries = list(entries)
    for i, entry in enumerate(updated_entries):
        if not needs_survey_overview(entry):
            continue
        overview = extract_survey_overview(client, entry)
        if overview is None:
            continue
        updated_entries[i] = {**entry, "survey_overview": overview}
        logger.info("survey_overviewを抽出しました: %s", entry.get("relative_path"))

    for entry in updated_entries:
        gs.write_release_page(entry, updated_entries, site_dir)

    return updated_entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際にgrade補正・ファイル移動(フェーズ1)とsurvey_overview抽出(フェーズ2)を実行する",
    )
    parser.add_argument(
        "--skip-overview",
        action="store_true",
        help="--apply時でもフェーズ2(survey_overview抽出)をスキップする",
    )
    parser.add_argument(
        "--site-dir",
        type=Path,
        default=SITE_DIR,
        help="対象サイトのディレクトリ(デフォルト: output/site)",
    )
    args = parser.parse_args()

    entries = gs.scan_existing_pages(args.site_dir)
    if not entries:
        logger.info("記事が見つかりませんでした")
        return

    corrected_entries = build_corrected_entries(entries)
    changed = [
        (old, new)
        for old, new in zip(entries, corrected_entries)
        if old.get("grade") != new.get("grade")
    ]

    logger.info("フェーズ1: grade不一致 %d件 / 全%d件", len(changed), len(entries))
    for old_entry, new_entry in changed:
        title = old_entry.get("raw_title") or old_entry.get("title", "")
        print(f"- {title!r}: {old_entry.get('grade')} -> {new_entry.get('grade')}")

    before = summarize_grade_distribution(entries)
    after = summarize_grade_distribution(corrected_entries)
    print(
        f"\ngrade分布: 現在 A={before.get('A', 0)} B={before.get('B', 0)} C={before.get('C', 0)}"
        f" -> 補正後 A={after.get('A', 0)} B={after.get('B', 0)} C={after.get('C', 0)}"
    )

    if not args.apply:
        logger.info(
            "dry-runモードのため書き換えは行いません(実行するには --apply を指定してください)"
        )
        return

    apply_grade_corrections(entries, corrected_entries, args.site_dir)
    logger.info("フェーズ1を適用しました(grade補正・ファイル移動・catchy_title再構築)")

    if args.skip_overview:
        logger.info(
            "--skip-overview が指定されたため、フェーズ2(survey_overview抽出)をスキップします"
        )
        return

    apply_survey_overview_extraction(corrected_entries, args.site_dir)
    logger.info("フェーズ2(survey_overview抽出)を適用しました")


if __name__ == "__main__":
    main()
