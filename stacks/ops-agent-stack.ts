import * as path from "node:path";
import * as cdk from "aws-cdk-lib/core";
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore";
import * as chatbot from "aws-cdk-lib/aws-chatbot";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
import * as cloudwatchActions from "aws-cdk-lib/aws-cloudwatch-actions";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as iam from "aws-cdk-lib/aws-iam";
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
  /** Slack からの依頼で遡れる期間の上限（時間）。Logs Insights のスキャン量の歯止め */
  maxLookbackHours: number;
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
 * 過去24時間の CloudWatch を自律調査して Slack に通知する運用エージェントのスタックを組み立てる。
 *
 * 構成: EventBridge Scheduler → 中継 Lambda → AgentCore Runtime (Strands Agents)
 *       → SNS → Amazon Q Developer in chat applications (旧 AWS Chatbot) → Slack
 */
export function createOpsAgentStack(
  scope: Construct,
  id: string,
  props: OpsAgentStackProps,
): cdk.Stack {
  const { parameters, ...stackProps } = props;
  const {
    scheduleCron,
    scheduleTimeZone,
    modelId,
    scoreThreshold,
    lookbackHours,
    maxLookbackHours,
    targetRegions,
    slackWorkspaceId,
    slackChannelId,
  } = parameters;

  const stack = new cdk.Stack(scope, id, stackProps);

  // 関数名は README と Slack の案内に実名で載せるため、自動生成に任せず決め打ちにする。
  // 名前を文字列で持つことで、エージェント（Runtime）から中継 Lambda を参照せずに済み、
  // Runtime → Lambda → Runtime の循環参照も避けられる
  const invokerFunctionName = `${stack.stackName}-invoker`;

  // ---- 通知先の SNS トピック ----
  const topic = new sns.Topic(stack, "NotificationTopic", {
    displayName: "ops-agent 日次ヘルスチェック通知",
  });

  // ---- エージェント本体 (AgentCore Runtime) ----
  const runtime = new agentcore.Runtime(stack, "AgentRuntime", {
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
      MAX_LOOKBACK_HOURS: String(maxLookbackHours),
      // 日次通知に載せる依頼コマンドの案内で使う。Amazon Q Developer は
      // リージョン未指定だと実行時に入力を求めてくるため、リージョンも渡す
      INVOKER_FUNCTION_NAME: invokerFunctionName,
      INVOKER_REGION: cdk.Aws.REGION,
      // コンテナ側の AWS_REGION に暗黙依存しないよう、未指定でも明示的に設定する
      TARGET_REGIONS:
        targetRegions.length > 0 ? targetRegions.join(",") : cdk.Aws.REGION,
    },
  });

  // CloudWatch の読み取りに必要な最小権限（調査ツールが使う API のみ）
  runtime.grant(
    [
      "cloudwatch:DescribeAlarms",
      "cloudwatch:DescribeAlarmHistory",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
      "logs:FilterLogEvents",
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
  const invoker = new lambda.Function(stack, "InvokerFunction", {
    description: "EventBridge Scheduler から AgentCore Runtime を起動する中継 Lambda",
    functionName: invokerFunctionName,
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
  // リトライによるエージェントの多重実行（重複通知）を防ぐ
  invoker.configureAsyncInvoke({ retryAttempts: 0 });

  // ---- 失敗に気づくためのアラーム ----
  // エージェントは自分で捕捉できた失敗を Slack へ通知して正常終了する。
  // ここで拾うのは、コンテナ障害やタイムアウトなど通知すら出せなかった失敗だけ
  const invokerErrorAlarm = new cloudwatch.Alarm(stack, "InvokerErrorAlarm", {
    alarmDescription:
      "運用エージェントの起動に失敗しました（エージェント側で通知できなかった失敗）",
    metric: invoker.metricErrors({
      period: cdk.Duration.minutes(5),
      statistic: "Sum",
    }),
    threshold: 1,
    evaluationPeriods: 1,
    comparisonOperator:
      cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
    // 実行は日次 1 回と依頼のときだけで、エラーが無い時間帯はデータ自体が無い
    treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
  });
  // 復旧は翌日の日次通知が届くことで分かるため、OK 遷移の通知は付けない
  invokerErrorAlarm.addAlarmAction(new cloudwatchActions.SnsAction(topic));

  // ---- 毎朝の定期実行 ----
  new scheduler.Schedule(stack, "DailySchedule", {
    description: "運用エージェントの日次実行",
    schedule: scheduler.ScheduleExpression.expression(
      scheduleCron,
      cdk.TimeZone.of(scheduleTimeZone),
    ),
    target: new schedulerTargets.LambdaInvoke(invoker, { retryAttempts: 0 }),
  });

  // ---- Slack 連携（任意） ----
  if (slackWorkspaceId && slackChannelId) {
    // Slack から実行できる操作は中継 Lambda の起動だけに絞る。
    // チャネルロールとガードレールの両方を明示しないと、ガードレールは
    // AdministratorAccess が既定になってしまう
    const invokeInvoker = () =>
      new iam.PolicyStatement({
        actions: ["lambda:InvokeFunction"],
        resources: [invoker.functionArn],
      });

    const slack = new chatbot.SlackChannelConfiguration(
      stack,
      "SlackNotification",
      {
        slackChannelConfigurationName: `${stack.stackName}-notifications`,
        slackWorkspaceId,
        slackChannelId,
        notificationTopics: [topic],
        guardrailPolicies: [
          new iam.ManagedPolicy(stack, "SlackCommandGuardrail", {
            description:
              "Slack から実行できる操作を運用エージェントの起動だけに限定する",
            statements: [invokeInvoker()],
          }),
        ],
      },
    );
    slack.addToRolePolicy(invokeInvoker());
  }

  new cdk.CfnOutput(stack, "InvokerFunctionName", {
    value: invokerFunctionName,
    description: "手動実行と Slack のエイリアス作成で使う中継 Lambda の関数名",
  });
  new cdk.CfnOutput(stack, "NotificationTopicArn", { value: topic.topicArn });
  new cdk.CfnOutput(stack, "AgentRuntimeArn", { value: runtime.agentRuntimeArn });

  return stack;
}
