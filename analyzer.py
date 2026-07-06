"""
classify_target.pyが出力した分類済みリリースを、Claude Batch APIで信頼性スコアリングするスクリプト。

処理の流れ:
  1. output/classified/YYYY-MM-DD.json を読み込む
  2. リリースごとにBatch APIリクエストを組み立てて送信
  3. バッチ完了までポーリング
  4. 結果(JSON)を取得し、各リリースに score フィールドとして付与
  5. output/scores/YYYY-MM-DD.json に保存

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

【catchy_titleの生成ルール】
サイト一覧・記事ページのタイトル表示用に、以下のルールで catchy_title を生成してください。
- 形式は「【{grade}評価・{total_score}点】{元の見出し}」とすること
  (ここでの grade・total_score は、このレスポンス自身が算出した grade・total_score の値と一致させること)
- 元の見出し自体の文言は改変しないこと(削除・要約・言い換え・語順の変更をしない)
- 元の見出しがそのまま数字や結論を含み、プレフィックスを付けると文として据わりが悪い場合に限り、
  末尾に「〜を検証」「〜の実態を確認」等、中立的で控えめな動詞句を軽く添えてもよい
  (例: 元の見出しが「約9割が期待する「資格取得支援」の実態調査」、grade=B、total_score=58 の場合
   → "【B評価・58点】約9割が期待する「資格取得支援」の実態調査、その信頼度を検証")
- 扇情的な煽り文句(「衝撃」「まさかの」「炎上」等)や誇張表現は一切使わないこと

【出力形式】
以下のJSON形式のみで出力してください。前置き・後書きは一切不要です。

{
  "total_score": 数値(0-100),
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
  "catchy_title": "上記【catchy_titleの生成ルール】に従って生成したタイトル"
}

【重要な制約】
- 断定的な信頼性の欠如を主張せず、開示情報の有無という客観的事実のみを根拠にすること
- 名誉毀損リスクを避けるため、企業名や個人への評価ではなく「この調査リリースの開示水準」への評価に徹すること
- 判断できない項目は減点ではなく「判定不能」として扱い、reasoningに明記すること
- 該当フラグが1つもない場合でも、reasoningを空文字にしてはならない。その場合は、各軸のスコアが満点でない理由、
  または満点に近い評価となった根拠(透明性が十分か、手法が明記されているか等)を、具体的に1〜2文で記述すること"""

# system promptの出力形式指示に対応するJSON Schema(Structured Outputsで形式を保証する)
SCORE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "total_score": {"type": "integer"},
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
        "catchy_title": {"type": "string"},
    },
    "required": [
        "total_score",
        "grade",
        "scores",
        "flags",
        "reasoning",
        "one_line_summary",
        "catchy_title",
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


def collect_results(client: anthropic.Anthropic, batch_id: str) -> dict[str, dict]:
    """バッチ結果を取得し、custom_idをキーにしたスコア辞書を返す。"""
    scores_by_id: dict[str, dict] = {}
    for result in client.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            text = next(
                (b.text for b in result.result.message.content if b.type == "text"),
                "",
            )
            try:
                scores_by_id[result.custom_id] = json.loads(text)
                if not scores_by_id[result.custom_id].get("reasoning", "").strip():
                    scores_by_id[result.custom_id]["reasoning"] = "特定不能（モデルが理由を生成できませんでした）"
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
    return scores_by_id


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
    scores_by_id = collect_results(client, batch_id)

    scored_releases: list[dict] = []
    for index, release in enumerate(releases):
        score = scores_by_id.get(custom_id_for(index), {"error": "missing_result"})
        scored_releases.append({**release, "score": score})

    SCORES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SCORES_DIR / input_path.name
    output_path.write_text(
        json.dumps(scored_releases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("保存しました: %s (%d件)", output_path, len(scored_releases))


if __name__ == "__main__":
    main()
