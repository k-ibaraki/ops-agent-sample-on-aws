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


def _format_notable(finding: Finding) -> str:
    return (
        f"*{finding.title}*（スコア {finding.score} / {finding.region}）\n"
        f"- 対象: `{finding.resource}`\n"
        f"- 状況: {finding.summary}\n"
        f"- 推奨: {finding.recommendation}"
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
        sections.append(f"--- スコア {threshold} 点以上の問題 ---")
        sections.extend(_format_notable(f) for f in notable)
    if minor:
        lines = "\n".join(f"- {f.title}（スコア {f.score} / {f.region}）" for f in minor)
        sections.append(f"--- その他の検出事項（{threshold} 点未満）---\n{lines}")

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
