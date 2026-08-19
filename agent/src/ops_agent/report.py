"""通知メッセージの整形。

Amazon Q Developer in chat applications（旧 AWS Chatbot）のカスタム通知形式で
SNS メッセージを組み立てる。LLM を介さない純粋なコードにしてテスト可能に保つ。
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ops_agent.models import AdhocReport, DailyReport, Finding

JST = timezone(timedelta(hours=9))

# カスタム通知の description が長すぎると表示が崩れるため余裕をもって切り詰める
MAX_DESCRIPTION_CHARS = 3500
MAX_SUBJECT_CHARS = 100
# 依頼内容はエージェントではなく利用者が書いた文字列なので、独立して長さを抑える
MAX_QUESTION_CHARS = 300
MAX_ERROR_CHARS = 500

# 問題同士の境目を示す区切り線
DIVIDER = "─" * 24

# 追加調査の依頼方法。README のコマンドエイリアス作成手順と対になっている
ADHOC_HINT = '*💬 追加調査を依頼できます*\n`@Amazon Q run ops "調べたい内容"`'


@dataclass(frozen=True)
class Notification:
    """SNS へ発行する通知（件名 + カスタム通知形式の JSON 文字列）。"""

    subject: str
    message: str


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _one_line(text: str) -> str:
    """件名に使えるよう、改行と連続空白を潰して1行にする。"""
    return " ".join(text.split())


def _custom_notification(title: str, description: str, subject: str) -> Notification:
    """Amazon Q Developer in chat applications のカスタム通知形式に包む。"""
    message = json.dumps(
        {
            "version": "1.0",
            "source": "custom",
            "content": {
                "textType": "client-markdown",
                "title": title,
                "description": description,
            },
        },
        ensure_ascii=False,
    )
    return Notification(subject=subject[:MAX_SUBJECT_CHARS], message=message)


def _severity_emoji(score: int) -> str:
    """スコア帯（システムプロンプトの採点基準と対応）を絵文字で表す。"""
    if score >= 90:
        return "🔴"
    if score >= 70:
        return "🟠"
    if score >= 50:
        return "🟡"
    if score >= 25:
        return "🔵"
    return "⚪"


def _format_notable(finding: Finding) -> str:
    # Slack の mrkdwn は太字 *...* の隣に全角文字が来ると崩れるため、太字は必ず行全体を包む。
    # 状況・推奨は複数行の本文が入る前提で、ラベル行 + 本文の段落に分ける
    return (
        f"*{_severity_emoji(finding.score)} {finding.title}*\n"
        f"スコア: {finding.score}（{finding.region}）\n"
        f"🎯 対象: `{finding.resource}`\n"
        f"\n"
        f"📊 状況:\n{finding.summary}\n"
        f"\n"
        f"💡 推奨:\n{finding.recommendation}"
    )


def build_notification(
    report: DailyReport, *, threshold: int, generated_at: datetime
) -> Notification:
    """日次レポートからカスタム通知形式の SNS メッセージを組み立てる。"""
    date_str = generated_at.astimezone(JST).strftime("%Y-%m-%d")
    notable = report.notable_findings(threshold)
    minor = [f for f in report.findings if f.score < threshold]

    if notable:
        title = f"🚨 日次ヘルスチェック {date_str}: 要注意 {len(notable)}件"
        subject_state = f"要注意 {len(notable)}件"
    else:
        title = f"✅ 日次ヘルスチェック {date_str}: 異常なし"
        subject_state = "異常なし"

    sections = [report.overall_summary]
    if notable:
        sections.append(f"*⚠️ スコア {threshold} 点以上の問題（{len(notable)}件）*")
        # 問題同士の境目が分かるよう、区切り線を挟む
        for i, finding in enumerate(notable):
            if i > 0:
                sections.append(DIVIDER)
            sections.append(_format_notable(finding))
    if minor:
        lines = "\n".join(
            f"{_severity_emoji(f.score)} {f.title}（スコア {f.score} / {f.region}）" for f in minor
        )
        sections.append(f"*📋 その他の検出事項（{threshold} 点未満）*\n{lines}")

    # 依頼例が切り詰めで消えないよう、本文を先に縮めてから連結する
    body = _clip("\n\n".join(sections), MAX_DESCRIPTION_CHARS - len(ADHOC_HINT) - 2)
    return _custom_notification(
        title=title,
        description=f"{body}\n\n{ADHOC_HINT}",
        subject=f"[ops-agent] 日次ヘルスチェック {date_str}: {subject_state}",
    )


def build_adhoc_notification(
    report: AdhocReport, *, question: str, generated_at: datetime
) -> Notification:
    """Slack から依頼された調査への回答を、カスタム通知形式に組み立てる。"""
    time_str = generated_at.astimezone(JST).strftime("%m-%d %H:%M")
    question_text = _clip(question.strip(), MAX_QUESTION_CHARS)

    sections = [f"*❓ 依頼内容*\n{question_text}", report.answer]
    if report.recommendations:
        lines = "\n".join(f"{i}. {r}" for i, r in enumerate(report.recommendations, 1))
        sections.append(f"*💡 推奨アクション*\n{lines}")
    if report.findings:
        findings = sorted(report.findings, key=lambda f: f.score, reverse=True)
        lines = "\n".join(
            f"{_severity_emoji(f.score)} {f.title}（スコア {f.score} / {f.region}）"
            for f in findings
        )
        sections.append(f"*📋 関連して見つかった問題*\n{lines}")

    return _custom_notification(
        title=f"🔎 調査依頼への回答 {time_str} JST",
        description=_clip("\n\n".join(sections), MAX_DESCRIPTION_CHARS),
        subject=f"[ops-agent] 調査依頼への回答: {_one_line(question_text)}",
    )


def build_failure_notification(
    *, question: str, error: str, generated_at: datetime
) -> Notification:
    """アドホック調査が失敗したことを依頼者に伝える通知を組み立てる。

    非同期実行のため、失敗を通知しないと依頼者は結果を待ち続けることになる。
    """
    time_str = generated_at.astimezone(JST).strftime("%m-%d %H:%M")
    question_text = _clip(question.strip(), MAX_QUESTION_CHARS)
    description = (
        f"*❓ 依頼内容*\n{question_text}\n\n"
        f"調査の途中でエラーが発生したため、回答を作成できませんでした。\n"
        f"時間をおいて再度依頼するか、実行ログを確認してください。\n\n"
        f"*⚠️ エラー内容*\n{_clip(error.strip(), MAX_ERROR_CHARS)}"
    )
    return _custom_notification(
        title=f"❌ 調査依頼への回答に失敗 {time_str} JST",
        description=_clip(description, MAX_DESCRIPTION_CHARS),
        subject=f"[ops-agent] 調査依頼への回答に失敗: {_one_line(question_text)}",
    )
