"""環境変数からの設定読み込み。"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

DEFAULT_MODEL_ID = "jp.anthropic.claude-sonnet-4-6"


@dataclass(frozen=True)
class Config:
    """エージェントの実行設定。CDK が設定する環境変数から生成する。"""

    sns_topic_arn: str
    model_id: str
    target_regions: tuple[str, ...]
    score_threshold: int
    lookback_hours: int
    max_lookback_hours: int
    # 通知に載せる依頼コマンドの案内に使う。どちらか欠けると案内は省く
    invoker_function_name: str
    invoker_region: str

    DEFAULT_SCORE_THRESHOLD: ClassVar[int] = 50
    DEFAULT_LOOKBACK_HOURS: ClassVar[int] = 24
    DEFAULT_MAX_LOOKBACK_HOURS: ClassVar[int] = 168

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        if env is None:
            env = os.environ

        sns_topic_arn = env.get("SNS_TOPIC_ARN", "").strip()
        if not sns_topic_arn:
            raise ValueError("環境変数 SNS_TOPIC_ARN が設定されていません")

        regions = tuple(r.strip() for r in env.get("TARGET_REGIONS", "").split(",") if r.strip())
        if not regions:
            default_region = env.get("AWS_REGION", "") or env.get("AWS_DEFAULT_REGION", "")
            if not default_region:
                raise ValueError(
                    "監視対象リージョンが特定できません"
                    "（TARGET_REGIONS か AWS_REGION を設定してください）"
                )
            regions = (default_region,)

        lookback_hours = int(env.get("LOOKBACK_HOURS", str(cls.DEFAULT_LOOKBACK_HOURS)))
        max_lookback_hours = int(env.get("MAX_LOOKBACK_HOURS", str(cls.DEFAULT_MAX_LOOKBACK_HOURS)))

        return cls(
            sns_topic_arn=sns_topic_arn,
            model_id=env.get("MODEL_ID", "").strip() or DEFAULT_MODEL_ID,
            target_regions=regions,
            score_threshold=int(env.get("SCORE_THRESHOLD", str(cls.DEFAULT_SCORE_THRESHOLD))),
            lookback_hours=lookback_hours,
            # 上限が既定の調査期間を下回ると日次チェックの期間まで縮んでしまう
            max_lookback_hours=max(max_lookback_hours, lookback_hours),
            invoker_function_name=env.get("INVOKER_FUNCTION_NAME", "").strip(),
            invoker_region=env.get("INVOKER_REGION", "").strip(),
        )
