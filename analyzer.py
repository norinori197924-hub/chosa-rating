"""
classify_target.pyが出力した分類済みリリースを、Claude Batch APIで信頼性スコアリングするスクリプト。

処理の流れ:
  1. output/classified/YYYY-MM-DD.json を読み込む
  2. リリースごとにBatch APIリクエストを組み立てて送信
  3. バッチ完了までポーリング
  4. 結果(JSON)を取得し、各リリースに score フィールドとして付与
  5. output/scores/YYYY-MM-DD.json に保存
  6. バッチのトークン使用量(入力/出力)を output/usage/YYYY-MM-DD.json に保存

環境変数 ANTHROPIC_API_KEY からAPIキーを取得する(anthropicライブラリの既定動作)。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("analyzer")

BASE_DIR = Path(__file__).resolve().parent
CLASSIFIED_DIR = BASE_DIR / "output" / "classified"
SCORES_DIR = BASE_DIR / "output" / "scores"
USAGE_DIR = BASE_DIR / "output" / "usage"

# コスト最優先でHaiku 4.5を使用
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 2048
POLL_INTERVAL_SEC = 30
POLL_TIMEOUT_SEC = 60 * 60  # 1時間でタイムアウト

SYSTEM_PROMPT = """あなたは市場調査の品質を評価する専門家です。20年以上マーケティングリサーチに従事してきた専門家の視点で、
入力された調査リリース(プレスリリース)の信頼性を、以下の5軸・各20点満点で採点してください。

【評価軸】
1. 調査主体の透明性(20点)
   - 調査実施会社名が明記されているか(0点:記載なし / 10点:自社名のみ / 20点:第三者調査会社名を明記)
   - 依頼主と調査実施主体が同一かどうかを明記しているか

2. 調査手法の開示(20点)
   - 調査方法(WEBアンケート/郵送/電話/対面等)の明記(0-7点)
   - 調査実施期間の明記(0-7点)
   - 調査対象地域・対象者条件の明記(0-6点)

3. サンプルの妥当性(20点)
   - サンプルサイズ(n数)が明記されているか(0点:非公開 / 10点:全体n数のみ / 20点:属性別n数まで開示)
   - 結論の強さに対してn数が十分か(n<30で断定的結論の場合は減点)

4. 利益相反(20点)
   - 依頼主の商品・サービス・業界と調査テーマの近さを評価
   - 「◯◯業界を代表する立場からの調査」等、販促目的が強い場合は減点
   - 完全に第三者的なテーマの場合は満点

5. 質問文・見出しの中立性(20点)
   - リリースの見出し・要約が、本文データから導ける結論と一致しているか
   - 質問文自体が誘導的でないか(全文が開示されている場合のみ評価。非開示の場合は12点を上限とする)

【調査概要の抽出】
本文から以下4項目を抽出してください。記載がなければ推測せず「記載なし」としてください(捏造禁止):
- target_respondents: 調査対象者
- question_count: 設問数
- sample_size: サンプル数(有効回答数)
- quota_allocation: 割付(性別・年代等の均等割付の有無)

【title_suffixの生成ルール】
grade と total_score は算出しないこと(Python側で機械計算する)。
かわりに、元の見出しに添える短い動詞句のみを title_suffix として生成してください。
- 例:「、その信頼度を検証」「、実態を確認」など、中立的で控えめなもの
- 不要と判断した場合は空文字でよい
- 扇情的な煽り文句(「衝撃」「まさかの」「炎上」等)は一切使わないこと

【出力形式】
以下のJSON形式のみで出力してください。前置き・後書きは一切不要です。

{
  "grade": "A" | "B" | "C",
  "scores": {
    "transparency": 数値,
    "methodology": 数値,
    "sample_validity": 数値,
    "conflict_of_interest": 数値,
    "neutrality": 数値
  },
  "flags": ["n数非公開", "自社調査", "誘導的見出し" など該当するものを配列で],
  "reasoning": "各軸で減点した理由を1-2文ずつ、事実ベースで簡潔に記述。主観的な断定表現(「怪しい」「信用できない」等)は使わず、「n数の記載が確認できない」「調査主体が依頼主と同一である」等の客観的表現に統一すること。",
  "one_line_summary": "サイト掲載用の一言コメント(20文字以内、事実ベース)",
  "title_suffix": "上記【title_suffixの生成ルール】に従って生成した短い動詞句(不要なら空文字)",
  "survey_overview": {
    "target_respondents": "調査対象者(記載なしの場合は「記載なし」)",
    "question_count": "設問数(記載なしの場合は「記載なし」)",
    "sample_size": "サンプル数・有効回答数(記載なしの場合は「記載なし」)",
    "quota_allocation": "割付の有無・内容(記載なしの場合は「記載なし」)"
  }
}

(total_score・catchy_titleはこのJSONには含めないこと。total_scoreはscoresの5軸合計から、
catchy_titleは元の見出し・grade・total_score・title_suffixから、別途システム側で機械的に組み立てる)

【重要な制約】
- 断定的な信頼性の欠如を主張せず、開示情報の有無という客観的事実のみを根拠にすること
- 名誉毀損リスクを避けるため、企業名や個人への評価ではなく「この調査リリースの開示水準」への評価に徹すること
- 判断できない項目は減点ではなく「判定不能」として扱い、reasoningに明記すること
- 該当フラグが1つもない場合でも、reasoningを空文字にしてはならない。その場合は、各軸のスコアが満点でない理由、
  または満点に近い評価となった根拠(透明性が十分か、手法が明記されているか等)を、具体的に1〜2文で記述すること"""

# 5軸のキー名。total_scoreはモデルには生成させず、この5軸の値をPython側で機械的に合計する
# (モデルが自己申告するtotal_scoreとscores内訳の合計が食い違う不具合を構造的に防ぐため)
SCORE_AXES = [
    "transparency",
    "methodology",
    "sample_validity",
    "conflict_of_interest",
    "neutrality",
]

# system promptの出力形式指示に対応するJSON Schema(Structured Outputsで形式を保証する)
# total_score・catchy_titleはモデルの出力対象から意図的に除外している
# (collect_results/main側でscoresの5軸合計・title_suffixから機械的に算出・組み立てる)
SCORE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "enum": ["A", "B", "C"]},
        "scores": {
            "type": "object",
            "properties": {
                "transparency": {"type": "integer"},
                "methodology": {"type": "integer"},
                "sample_validity": {"type": "integer"},
                "conflict_of_interest": {"type": "integer"},
                "neutrality": {"type": "integer"},
            },
            "required": [
                "transparency",
                "methodology",
                "sample_validity",
                "conflict_of_interest",
                "neutrality",
            ],
            "additionalProperties": False,
        },
        "flags": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string", "minLength": 1},
        "one_line_summary": {"type": "string"},
        "title_suffix": {"type": "string"},
        "survey_overview": {
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
        },
    },
    "required": [
        "grade",
        "scores",
        "flags",
        "reasoning",
        "one_line_summary",
        "title_suffix",
        "survey_overview",
    ],
    "additionalProperties": False,
}


def build_user_content(release: dict) -> str:
    """リリース情報からユーザーターンのテキストを組み立てる。"""
    return (
        "以下の調査リリースを評価してください。\n\n"
        f"タイトル: {release.get('title', '')}\n"
        f"配信元: {release.get('source', '')}\n"
        f"URL: {release.get('url', '')}\n"
        f"公開日: {release.get('published_date', '')}\n"
        f"想定ターゲット: {release.get('target', '')}\n\n"
        "本文:\n"
        f"{release.get('body_text', '')}"
    )


def calculate_grade(total_score: int) -> str:
    """total_score(0-100)から、開示水準に基づくgradeを算出する。

    A: 70点以上 / B: 40〜69点 / C: 40点未満。
    """
    if total_score >= 70:
        return "A"
    elif total_score >= 40:
        return "B"
    else:
        return "C"


def build_catchy_title(raw_title: str, score: dict) -> str:
    """raw_title・Python側で確定させたgrade/total_score・モデルが生成したtitle_suffixから、
    表示用タイトル(catchy_title)を組み立てる。

    grade・total_scoreはモデルの自己申告ではなく、この関数に渡すscore辞書内の確定値
    (collect_resultsが機械計算したtotal_score、モデルが出力したgrade)をそのまま使うため、
    タイトル文中の点数表示とJSON側のtotal_scoreが食い違うことがない。
    """
    grade = score.get("grade", "")
    total_score = score.get("total_score", "")
    suffix = score.get("title_suffix", "") or ""
    if grade not in ("A", "B", "C") or not isinstance(total_score, (int, float)):
        return raw_title
    return f"【{grade}評価・{total_score}点】{raw_title}{suffix}"


def custom_id_for(index: int) -> str:
    """インデックスからBatch APIのcustom_idを生成する。"""
    return f"release-{index:04d}"


def build_batch_requests(releases: list[dict]) -> list[Request]:
    """リリースのリストからBatch APIリクエストのリストを組み立てる。"""
    requests: list[Request] = []
    for index, release in enumerate(releases):
        requests.append(
            Request(
                custom_id=custom_id_for(index),
                params=MessageCreateParamsNonStreaming(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    output_config={
                        "format": {
                            "type": "json_schema",
                            "schema": SCORE_JSON_SCHEMA,
                        }
                    },
                    messages=[
                        {"role": "user", "content": build_user_content(release)}
                    ],
                ),
            )
        )
    return requests


def submit_batch(client: anthropic.Anthropic, requests: list[Request]) -> str:
    """Batch APIにリクエストを送信し、バッチIDを返す。"""
    batch = client.messages.batches.create(requests=requests)
    logger.info("バッチを作成しました: %s (%d件)", batch.id, len(requests))
    return batch.id


def wait_for_batch(client: anthropic.Anthropic, batch_id: str) -> None:
    """バッチの処理完了(processing_status == 'ended')までポーリングする。"""
    elapsed = 0
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        logger.info(
            "バッチ状況: %s (処理中:%d 成功:%d 失敗:%d)",
            batch.processing_status,
            counts.processing,
            counts.succeeded,
            counts.errored,
        )
        if batch.processing_status == "ended":
            return
        if elapsed >= POLL_TIMEOUT_SEC:
            raise TimeoutError(f"バッチ処理がタイムアウトしました: {batch_id}")
        time.sleep(POLL_INTERVAL_SEC)
        elapsed += POLL_INTERVAL_SEC


def collect_results(
    client: anthropic.Anthropic, batch_id: str
) -> tuple[dict[str, dict], dict[str, int]]:
    """バッチ結果を取得し、custom_idをキーにしたスコア辞書と、トークン使用量の合計を返す。"""
    scores_by_id: dict[str, dict] = {}
    usage_totals = {"input_tokens": 0, "output_tokens": 0}
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            message = result.result.message
            usage_totals["input_tokens"] += message.usage.input_tokens
            usage_totals["output_tokens"] += message.usage.output_tokens
            text = next(
                (b.text for b in message.content if b.type == "text"),
                "",
            )
            try:
                scores_by_id[result.custom_id] = json.loads(text)
                if not scores_by_id[result.custom_id].get("reasoning", "").strip():
                    scores_by_id[result.custom_id]["reasoning"] = "特定不能（モデルが理由を生成できませんでした）"
                # total_scoreはモデルに生成させず、scoresの5軸合計から機械的に算出する
                axis_scores = scores_by_id[result.custom_id].get("scores", {})
                scores_by_id[result.custom_id]["total_score"] = sum(
                    axis_scores.get(axis, 0) for axis in SCORE_AXES
                )
            except json.JSONDecodeError:
                logger.error("JSONのパースに失敗しました: %s", result.custom_id)
                scores_by_id[result.custom_id] = {"error": "invalid_json", "raw": text}
        elif result.result.type == "errored":
            logger.error(
                "リクエストがエラーになりました: %s (%s)",
                result.custom_id,
                result.result.error.type,
            )
            scores_by_id[result.custom_id] = {"error": result.result.error.type}
        else:
            logger.warning(
                "未処理の結果タイプ: %s (%s)", result.custom_id, result.result.type
            )
            scores_by_id[result.custom_id] = {"error": result.result.type}
    return scores_by_id, usage_totals


def find_input_path(date_str: str | None) -> Path:
    """入力JSONファイルのパスを決定する。指定がなければ最新のファイルを使う。"""
    if date_str:
        path = CLASSIFIED_DIR / f"{date_str}.json"
        if not path.exists():
            raise FileNotFoundError(f"指定日のファイルが見つかりません: {path}")
        return path

    candidates = sorted(CLASSIFIED_DIR.glob("*.json"))
    if not candidates:
        raise FileNotFoundError(f"入力ファイルが見つかりません: {CLASSIFIED_DIR}")
    return candidates[-1]


def main() -> None:
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    input_path = find_input_path(date_arg)

    logger.info("読み込み中: %s", input_path)
    releases = json.loads(input_path.read_text(encoding="utf-8"))
    if not releases:
        logger.info("対象リリースがないため処理をスキップします")
        return

    client = anthropic.Anthropic()

    requests = build_batch_requests(releases)
    batch_id = submit_batch(client, requests)
    wait_for_batch(client, batch_id)
    scores_by_id, usage_totals = collect_results(client, batch_id)

    scored_releases: list[dict] = []
    for index, release in enumerate(releases):
        score = scores_by_id.get(custom_id_for(index), {"error": "missing_result"})
        if "error" not in score:
            score = {**score, "catchy_title": build_catchy_title(release.get("title", ""), score)}
        scored_releases.append({**release, "score": score})

    SCORES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SCORES_DIR / input_path.name
    output_path.write_text(
        json.dumps(scored_releases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("保存しました: %s (%d件)", output_path, len(scored_releases))

    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    usage_path = USAGE_DIR / input_path.name
    usage_path.write_text(
        json.dumps(
            {
                "date": input_path.stem,
                "batch_id": batch_id,
                "model": MODEL,
                "request_count": len(releases),
                "input_tokens": usage_totals["input_tokens"],
                "output_tokens": usage_totals["output_tokens"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(
        "トークン使用量を記録しました: %s (入力%d件/出力%d件)",
        usage_path,
        usage_totals["input_tokens"],
        usage_totals["output_tokens"],
    )


if __name__ == "__main__":
    main()
