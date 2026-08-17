"""SNS 発行のテスト。"""

from typing import Any

from ops_agent.notifier import publish_notification
from ops_agent.report import Notification


class FakeSns:
    def __init__(self) -> None:
        self.publish_kwargs: dict[str, Any] = {}

    def publish(self, **kwargs: Any) -> dict[str, Any]:
        self.publish_kwargs = kwargs
        return {"MessageId": "msg-1"}


def test_通知をSNSトピックへ発行する() -> None:
    sns = FakeSns()
    notification = Notification(subject="件名", message='{"version": "1.0"}')

    message_id = publish_notification(
        sns,
        topic_arn="arn:aws:sns:ap-northeast-1:123456789012:topic",
        notification=notification,
    )

    assert message_id == "msg-1"
    assert sns.publish_kwargs == {
        "TopicArn": "arn:aws:sns:ap-northeast-1:123456789012:topic",
        "Subject": "件名",
        "Message": '{"version": "1.0"}',
    }
