"""通知メッセージの整形。

Amazon Q Developer in chat applications（旧 AWS Chatbot）のカスタム通知形式で
SNS メッセージを組み立てる。LLM を介さない純粋なコードにしてテスト可能に保つ。
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ops_agent.models import DailyReport, Finding

JST = timezone(timedelta(hours=9))

# カスタム通知の description が長すぎると表示が崩れるため余裕をもって切り詰める
MAX_DESCRIPTION_CHARS = 3500
MAX_SUBJECT_CHARS = 100


@dataclass(frozen=True)
class Notification:
    """SNS へ発行する通知（件名 + カスタム通知形式の JSON 文字列）。"""

    subject: str
    message: str


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
        sections.append(f"*⚠️ スコア {threshold} 点以上の問題*")
        sections.extend(_format_notable(f) for f in notable)
    if minor:
        lines = "\n".join(
            f"{_severity_emoji(f.score)} {f.title}（スコア {f.score} / {f.region}）" for f in minor
        )
        sections.append(f"*📋 その他の検出事項（{threshold} 点未満）*\n{lines}")

    description = "\n\n".join(sections)
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[: MAX_DESCRIPTION_CHARS - 1] + "…"

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
    subject = f"[ops-agent] 日次ヘルスチェック {date_str}: {subject_state}"[:MAX_SUBJECT_CHARS]
    return Notification(subject=subject, message=message)
