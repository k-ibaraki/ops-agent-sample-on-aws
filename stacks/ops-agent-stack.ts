import * as path from "node:path";
import * as cdk from "aws-cdk-lib/core";
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore";
import * as chatbot from "aws-cdk-lib/aws-chatbot";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as scheduler from "aws-cdk-lib/aws-scheduler";
import * as schedulerTargets from "aws-cdk-lib/aws-scheduler-targets";
import * as sns from "aws-cdk-lib/aws-sns";
import type { Construct } from "constructs";

/** デプロイパラメータ。リポジトリ直下の parameter.ts（parameter.sample.ts のコピー）で設定する */
export interface OpsAgentParameters {
  /** 実行スケジュール（EventBridge Scheduler の cron 式） */
  scheduleCron: string;
  /** スケジュールのタイムゾーン */
  scheduleTimeZone: string;
  /** エージェントが使う Bedrock モデル ID */
  modelId: string;
  /** 通知で強調するスコアの閾値（0〜100） */
  scoreThreshold: number;
  /** 調査対象の期間（時間） */
  lookbackHours: number;
  /** 監視対象リージョン。空配列ならデプロイ先リージョンのみ */
  targetRegions: string[];
  /** Slack ワークスペース ID（T で始まる）。channelId とセットで指定すると Slack 連携を作成 */
  slackWorkspaceId?: string;
  /** Slack チャンネル ID（C で始まる） */
  slackChannelId?: string;
}

export interface OpsAgentStackProps extends cdk.StackProps {
  /** デプロイパラメータ */
  parameters: OpsAgentParameters;
}

/**
 * 過去24時間の CloudWatch を自律調査して Slack に通知する運用エージェントのスタック。
 *
 * 構成: EventBridge Scheduler → 中継 Lambda → AgentCore Runtime (Strands Agents)
 *       → SNS → Amazon Q Developer in chat applications (旧 AWS Chatbot) → Slack
 */
export class OpsAgentStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: OpsAgentStackProps) {
    super(scope, id, props);

    const {
      scheduleCron,
      scheduleTimeZone,
      modelId,
      scoreThreshold,
      lookbackHours,
      targetRegions,
      slackWorkspaceId,
      slackChannelId,
    } = props.parameters;

    // ---- 通知先の SNS トピック ----
    const topic = new sns.Topic(this, "NotificationTopic", {
      displayName: "ops-agent 日次ヘルスチェック通知",
    });

    // ---- エージェント本体 (AgentCore Runtime) ----
    const runtime = new agentcore.Runtime(this, "AgentRuntime", {
      description: "CloudWatch を日次チェックする運用エージェント",
      agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromAsset(
        path.join(__dirname, "..", "agent"),
        { platform: ecrAssets.Platform.LINUX_ARM64 },
      ),
      environmentVariables: {
        SNS_TOPIC_ARN: topic.topicArn,
        MODEL_ID: modelId,
        SCORE_THRESHOLD: String(scoreThreshold),
        LOOKBACK_HOURS: String(lookbackHours),
        ...(targetRegions.length > 0
          ? { TARGET_REGIONS: targetRegions.join(",") }
          : {}),
      },
    });

    // CloudWatch の読み取りに必要な最小権限（調査ツールが使う API のみ）
    runtime.grant(
      [
        "cloudwatch:DescribeAlarms",
        "cloudwatch:DescribeAlarmHistory",
        "cloudwatch:GetMetricStatistics",
        "logs:DescribeLogGroups",
        "logs:StartQuery",
        "logs:GetQueryResults",
        "logs:StopQuery",
      ],
      ["*"],
    );
    // Bedrock モデル呼び出し（クロスリージョン推論プロファイル経由）
    runtime.grant(
      ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      [
        `arn:${cdk.Aws.PARTITION}:bedrock:*::foundation-model/*`,
        `arn:${cdk.Aws.PARTITION}:bedrock:*:${cdk.Aws.ACCOUNT_ID}:inference-profile/*`,
      ],
    );
    topic.grantPublish(runtime);

    // ---- 中継 Lambda（Scheduler から起動され Runtime を同期呼び出しする） ----
    const invoker = new lambda.Function(this, "InvokerFunction", {
      description: "EventBridge Scheduler から AgentCore Runtime を起動する中継 Lambda",
      runtime: lambda.Runtime.PYTHON_3_14,
      architecture: lambda.Architecture.ARM_64,
      handler: "handler.handler",
      code: lambda.Code.fromAsset(path.join(__dirname, "..", "invoker"), {
        exclude: ["__pycache__", "*.pyc"],
      }),
      // エージェントの調査完了まで同期で待つため、Lambda の上限いっぱいに設定
      timeout: cdk.Duration.minutes(15),
      memorySize: 256,
      environment: {
        AGENT_RUNTIME_ARN: runtime.agentRuntimeArn,
      },
    });
    runtime.grantInvokeRuntime(invoker);

    // ---- 毎朝の定期実行 ----
    new scheduler.Schedule(this, "DailySchedule", {
      description: "運用エージェントの日次実行",
      schedule: scheduler.ScheduleExpression.expression(
        scheduleCron,
        cdk.TimeZone.of(scheduleTimeZone),
      ),
      target: new schedulerTargets.LambdaInvoke(invoker),
    });

    // ---- Slack 連携（任意） ----
    if (slackWorkspaceId && slackChannelId) {
      new chatbot.SlackChannelConfiguration(this, "SlackNotification", {
        slackChannelConfigurationName: `${this.stackName}-notifications`,
        slackWorkspaceId,
        slackChannelId,
        notificationTopics: [topic],
      });
    }

    new cdk.CfnOutput(this, "NotificationTopicArn", { value: topic.topicArn });
    new cdk.CfnOutput(this, "AgentRuntimeArn", { value: runtime.agentRuntimeArn });
  }
}
