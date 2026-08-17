"""Config（環境変数からの設定読み込み）のテスト。"""

import pytest

from ops_agent.config import Config

BASE_ENV = {
    "SNS_TOPIC_ARN": "arn:aws:sns:ap-northeast-1:123456789012:ops-agent-topic",
    "AWS_REGION": "ap-northeast-1",
}


def test_必須項目とデフォルト値が反映される() -> None:
    config = Config.from_env(BASE_ENV)

    assert config.sns_topic_arn == BASE_ENV["SNS_TOPIC_ARN"]
    assert config.model_id == "jp.anthropic.claude-sonnet-4-6"
    assert config.target_regions == ("ap-northeast-1",)
    assert config.score_threshold == 50
    assert config.lookback_hours == 24


def test_環境変数で上書きできる() -> None:
    env = BASE_ENV | {
        "MODEL_ID": "jp.anthropic.claude-haiku-4-5",
        "TARGET_REGIONS": "ap-northeast-1, us-east-1,,",
        "SCORE_THRESHOLD": "70",
        "LOOKBACK_HOURS": "12",
    }

    config = Config.from_env(env)

    assert config.model_id == "jp.anthropic.claude-haiku-4-5"
    assert config.target_regions == ("ap-northeast-1", "us-east-1")
    assert config.score_threshold == 70
    assert config.lookback_hours == 12


def test_SNSトピック未設定はエラーになる() -> None:
    with pytest.raises(ValueError, match="SNS_TOPIC_ARN"):
        Config.from_env({"AWS_REGION": "ap-northeast-1"})


def test_リージョンが特定できない場合はエラーになる() -> None:
    with pytest.raises(ValueError, match="リージョン"):
        Config.from_env({"SNS_TOPIC_ARN": BASE_ENV["SNS_TOPIC_ARN"]})
