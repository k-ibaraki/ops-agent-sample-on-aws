"""SNS への通知発行。"""

from typing import Any

from ops_agent.report import Notification


def publish_notification(sns_client: Any, *, topic_arn: str, notification: Notification) -> str:
    """通知を SNS トピックへ発行し、MessageId を返す。"""
    response = sns_client.publish(
        TopicArn=topic_arn,
        Subject=notification.subject,
        Message=notification.message,
    )
    return response["MessageId"]
