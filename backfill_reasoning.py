"""
output/site 配下の記事ページのうち、release-metaのreasoningが空文字になっているものを検出し、
Claude API(analyzer.pyと同じモデル)でreasoningだけを再生成して該当ページを書き換えるスクリプト。

再生成に使う情報はrelease-meta JSONに既に入っている以下のみとする(本文の再取得はしない):
  - title / one_line_summary / scores(5軸) / flags / body_text(400文字まで)
スコア・flagsは変更せず、reasoningフィールドのみを差し替える。

書き換え対象は1ページにつき2箇所:
  1. <script id="release-meta"> 内のJSONの "reasoning"
  2. 本文の <h2 class="section-title">評価理由</h2> 直後の <p>...</p>

デフォルトはdry-runで、対象ページのタイトル一覧と現在のreasoning(空であること)を表示するのみ。
実際にAPIを呼んで書き換えるには --apply を指定する。
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from html import escape as h
from pathlib import Path

import anthropic

from analyzer import MAX_TOKENS, MODEL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill_reasoning")

BASE_DIR = Path(__file__).resolve().parent
SITE_DIR = BASE_DIR / "output" / "site"

_META_SCRIPT_PATTERN = re.compile(
    r'(<script type="application/json" id="release-meta">)(.*?)(</script>)', re.DOTALL
)
_REASONING_BLOCK_PATTERN = re.compile(
    r'(<h2 class="section-title">評価理由</h2>\s*<p>)(.*?)(</p>)', re.DOTALL
)

# analyzer.py SYSTEM_PROMPT のreasoning記述ルール(93・98-101行目付近)を、
# 「スコア・フラグ確定済み、reasoningのみ生成」というこのスクリプト用のタスクに合わせて流用したもの。
SYSTEM_PROMPT = """あなたは市場調査の品質を評価する専門家です。以下の調査リリースについて、
5軸の採点(信頼性評価)と該当フラグは既に確定しています。この確定済みのスコア・フラグに基づいて、
reasoning(評価理由)のみを生成してください。

【reasoningの書き方】
- 各軸のスコアが満点でない理由、または満点に近い評価となった根拠を、事実ベースで1〜2文に簡潔にまとめること
- 主観的な断定表現(「怪しい」「信用できない」等)は使わず、「n数の記載が確認できない」「調査主体が依頼主と同一である」等の客観的表現に統一すること
- 断定的な信頼性の欠如を主張せず、開示情報の有無という客観的事実のみを根拠にすること
- 名誉毀損リスクを避けるため、企業名や個人への評価ではなく「この調査リリースの開示水準」への評価に徹すること
- 判断できない項目は「判定不能」として明記すること
- 該当フラグが1つもない場合でも、reasoningを空文字にしてはならない。透明性が十分か、手法が明記されているかなど、
  スコアの根拠を具体的に記述すること

【出力形式】
reasoningの本文のみをプレーンテキストで出力してください。前置き・後書き・JSON形式・引用符・見出しは一切不要です。"""


def find_target_pages(site_dir: Path) -> list[dict]:
    """reasoningが空文字のページを探索し、パスとmetaの組を返す。"""
    targets: list[dict] = []
    if not site_dir.exists():
        return targets

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
                try:
                    meta = json.loads(match.group(2))
                except json.JSONDecodeError:
                    logger.warning("メタデータのパースに失敗しました: %s", page_path)
                    continue

                if meta.get("reasoning", "").strip():
                    continue
                targets.append({"path": page_path, "meta": meta})

    return targets


def build_user_content(meta: dict) -> str:
    """release-metaの情報のみからreasoning生成用のユーザーターンを組み立てる。"""
    title = meta.get("raw_title") or meta.get("title", "")
    flags = meta.get("flags") or []
    return (
        f"タイトル: {title}\n"
        f"一言コメント: {meta.get('one_line_summary', '')}\n"
        f"スコア(5軸、各軸20点満点): {json.dumps(meta.get('scores', {}), ensure_ascii=False)}\n"
        f"該当フラグ: {'、'.join(flags) if flags else 'なし'}\n\n"
        "本文(抜粋):\n"
        f"{(meta.get('body_text') or '')[:400]}"
    )


def generate_reasoning(client: anthropic.Anthropic, meta: dict) -> str:
    """Claude APIを呼び出し、reasoningの本文テキストを生成する。"""
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_content(meta)}],
    )
    text = next((b.text for b in response.content if b.type == "text"), "")
    return text.strip()


def apply_reasoning(page_path: Path, meta: dict, reasoning: str) -> None:
    """release-metaのJSONと本文表示、両方のreasoningを書き換えてページを保存する。"""
    text = page_path.read_text(encoding="utf-8")

    updated_meta = {**meta, "reasoning": reasoning}
    raw = json.dumps(updated_meta, ensure_ascii=False)
    raw = raw.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    text, meta_count = _META_SCRIPT_PATTERN.subn(
        lambda m: f"{m.group(1)}{raw}{m.group(3)}", text, count=1
    )
    if meta_count == 0:
        logger.warning("release-metaの置換に失敗しました: %s", page_path)
        return

    text, body_count = _REASONING_BLOCK_PATTERN.subn(
        lambda m: f"{m.group(1)}{h(reasoning)}{m.group(3)}", text, count=1
    )
    if body_count == 0:
        logger.warning("本文の評価理由の置換に失敗しました: %s", page_path)
        return

    page_path.write_text(text, encoding="utf-8")


def load_page_meta(page_path: Path) -> dict | None:
    """指定ページのrelease-metaをreasoningの中身に関わらず読み込む(--page指定時に使用)。"""
    text = page_path.read_text(encoding="utf-8")
    match = _META_SCRIPT_PATTERN.search(text)
    if not match:
        logger.error("メタデータが見つかりません: %s", page_path)
        return None
    try:
        return json.loads(match.group(2))
    except json.JSONDecodeError:
        logger.error("メタデータのパースに失敗しました: %s", page_path)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際にAPIを呼んでページを書き換える(指定しなければdry-run表示のみ)",
    )
    parser.add_argument(
        "--site-dir",
        type=Path,
        default=SITE_DIR,
        help="対象サイトのディレクトリ(デフォルト: output/site)",
    )
    parser.add_argument(
        "--page",
        type=Path,
        default=None,
        help=(
            "reasoningの中身に関わらず、指定した1ページのみを対象にする"
            "(--site-dirからの相対パス、またはHTMLファイルへの絶対パス)"
        ),
    )
    args = parser.parse_args()

    if args.page:
        page_path = args.page if args.page.is_absolute() else args.site_dir / args.page
        if not page_path.exists():
            logger.error("指定されたページが見つかりません: %s", page_path)
            return
        meta = load_page_meta(page_path)
        if meta is None:
            return
        targets = [{"path": page_path, "meta": meta}]
    else:
        targets = find_target_pages(args.site_dir)
        if not targets:
            logger.info("reasoningが空文字のページは見つかりませんでした")
            return

    logger.info("対象ページ: %d件", len(targets))
    for target in targets:
        meta = target["meta"]
        title = meta.get("raw_title") or meta.get("title", "")
        relative_path = target["path"].relative_to(args.site_dir)
        print(f"- {relative_path} : {title!r} (reasoning={meta.get('reasoning', '')!r})")

    if not args.apply:
        logger.info("dry-runモードのため書き換えは行いません(実行するには --apply を指定してください)")
        return

    client = anthropic.Anthropic()
    for target in targets:
        page_path = target["path"]
        meta = target["meta"]
        reasoning = generate_reasoning(client, meta)
        if not reasoning:
            logger.warning("reasoningの生成に失敗しました(空応答): %s", page_path)
            continue
        apply_reasoning(page_path, meta, reasoning)
        logger.info("更新しました: %s", page_path)


if __name__ == "__main__":
    main()
