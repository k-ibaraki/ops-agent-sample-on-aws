import * as cdk from "aws-cdk-lib/core";
import { Match, Template } from "aws-cdk-lib/assertions";
import { parameters } from "../parameter.sample";
import {
  createOpsAgentStack,
  type OpsAgentParameters,
} from "../stacks/ops-agent-stack";

function synth(overrides: Partial<OpsAgentParameters> = {}): Template {
  const app = new cdk.App();
  const stack = createOpsAgentStack(app, "TestStack", {
    parameters: { ...parameters, ...overrides },
  });
  return Template.fromStack(stack);
}

describe("OpsAgentStack", () => {
  let template: Template;

  beforeAll(() => {
    template = synth();
  });

  test("通知用の SNS トピックが作成される", () => {
    template.resourceCountIs("AWS::SNS::Topic", 1);
  });

  test("AgentCore Runtime が設定入りの環境変数つきで作成される", () => {
    template.hasResourceProperties("AWS::BedrockAgentCore::Runtime", {
      EnvironmentVariables: Match.objectLike({
        MODEL_ID: "jp.anthropic.claude-sonnet-4-6",
        SCORE_THRESHOLD: "50",
        LOOKBACK_HOURS: "24",
        SNS_TOPIC_ARN: Match.anyValue(),
        // targetRegions 未指定でもデプロイ先リージョンが明示的に設定される
        TARGET_REGIONS: { Ref: "AWS::Region" },
      }),
    });
  });

  test("Runtime の実行ロールに CloudWatch 読み取りの最小権限が付与される", () => {
    // 調査ツール用のステートメントが読み取り専用アクションだけで構成されていること
    template.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: [
              "cloudwatch:DescribeAlarms",
              "cloudwatch:DescribeAlarmHistory",
              "cloudwatch:GetMetricStatistics",
              "logs:DescribeLogGroups",
              "logs:StartQuery",
              "logs:GetQueryResults",
              "logs:StopQuery",
            ],
            Effect: "Allow",
            Resource: "*",
          }),
        ]),
      }),
    });

    const json = JSON.stringify(template.toJSON());
    expect(json).toContain("bedrock:InvokeModel");
    expect(json).toContain("sns:Publish");
    // 書き込み系の権限を付与していないこと
    expect(json).not.toContain("cloudwatch:PutMetricAlarm");
    expect(json).not.toContain("logs:DeleteLogGroup");
  });

  test("中継 Lambda が Python 3.14・15 分タイムアウトで作成される", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Runtime: "python3.14",
      Handler: "handler.handler",
      Timeout: 900,
      Environment: {
        Variables: Match.objectLike({
          AGENT_RUNTIME_ARN: Match.anyValue(),
        }),
      },
    });
  });

  test("中継 Lambda に InvokeAgentRuntime の権限が付与される", () => {
    expect(JSON.stringify(template.toJSON())).toContain(
      "bedrock-agentcore:InvokeAgentRuntime",
    );
  });

  test("リトライによる多重実行が無効化されている", () => {
    // Lambda の非同期リトライと Scheduler のリトライの両方を止める
    template.hasResourceProperties("AWS::Lambda::EventInvokeConfig", {
      MaximumRetryAttempts: 0,
    });
    template.hasResourceProperties("AWS::Scheduler::Schedule", {
      Target: Match.objectLike({
        RetryPolicy: Match.objectLike({ MaximumRetryAttempts: 0 }),
      }),
    });
  });

  test("毎朝 8:00 JST のスケジュールが作成される", () => {
    template.hasResourceProperties("AWS::Scheduler::Schedule", {
      ScheduleExpression: "cron(0 8 * * ? *)",
      ScheduleExpressionTimezone: "Asia/Tokyo",
    });
  });

  test("cron 式は parameter.ts で変更できる", () => {
    const custom = synth({ scheduleCron: "cron(30 6 * * ? *)" });
    custom.hasResourceProperties("AWS::Scheduler::Schedule", {
      ScheduleExpression: "cron(30 6 * * ? *)",
    });
  });

  test("デフォルトでは Slack 連携 (Chatbot) は作成されない", () => {
    template.resourceCountIs("AWS::Chatbot::SlackChannelConfiguration", 0);
  });

  test("workspace/channel ID を指定すると Slack 連携が作成される", () => {
    const withSlack = synth({
      slackWorkspaceId: "T0123456789",
      slackChannelId: "C0123456789",
    });
    withSlack.hasResourceProperties("AWS::Chatbot::SlackChannelConfiguration", {
      SlackWorkspaceId: "T0123456789",
      SlackChannelId: "C0123456789",
      SnsTopicArns: Match.anyValue(),
    });
  });

  test("監視対象リージョンは parameter.ts で設定できる", () => {
    const multi = synth({ targetRegions: ["ap-northeast-1", "us-east-1"] });
    multi.hasResourceProperties("AWS::BedrockAgentCore::Runtime", {
      EnvironmentVariables: Match.objectLike({
        TARGET_REGIONS: "ap-northeast-1,us-east-1",
      }),
    });
  });
});
