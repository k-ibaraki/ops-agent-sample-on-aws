import { readFileSync } from "node:fs";
import * as path from "node:path";
import * as cdk from "aws-cdk-lib/core";
import { Match, Template } from "aws-cdk-lib/assertions";
import { parameters } from "../parameter.sample";
import {
  createOpsAgentStack,
  type OpsAgentParameters,
} from "../stacks/ops-agent-stack";

// new cdk.App() は cdk.json を読まないため、feature flag を渡して本番の synth と揃える
// （中継 Lambda のロググループのように、フラグで生成されるリソースがあるため）
const { context } = JSON.parse(
  readFileSync(path.join(__dirname, "..", "cdk.json"), "utf-8"),
);

function synth(overrides: Partial<OpsAgentParameters> = {}): Template {
  const app = new cdk.App({ context });
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
        MAX_LOOKBACK_HOURS: "168",
        SNS_TOPIC_ARN: Match.anyValue(),
        // targetRegions 未指定でもデプロイ先リージョンが明示的に設定される
        TARGET_REGIONS: { Ref: "AWS::Region" },
      }),
    });
  });

  test("Runtime の実行ロールに CloudWatch 読み取りの最小権限が付与される", () => {
    // 実行ロールに付くアクションが、自前で付けたものと L2 construct の既定分だけであること
    // （書き込み系が足されたら落ちる）。minimizePolicies でステートメントは統合・並べ替え
    // されるため、形ではなくアクションの集合で見る
    const roleId = template.getResourceId("AWS::IAM::Role", {
      Properties: {
        AssumeRolePolicyDocument: {
          Statement: Match.arrayWith([
            Match.objectLike({
              Principal: { Service: "bedrock-agentcore.amazonaws.com" },
            }),
          ]),
        },
      },
    });
    // 他のロールと共有するポリシーも拾うため、Roles は「実行ロールを含む」で絞る
    const attachedToRuntimeRole = {
      Properties: { Roles: Match.arrayWith([{ Ref: roleId }]) },
    };
    // 管理ポリシー経由の権限は下の集計に現れないため、付いていないことを確かめる
    expect(
      template.findResources("AWS::IAM::ManagedPolicy", attachedToRuntimeRole),
    ).toEqual({});
    expect(
      template.toJSON().Resources[roleId].Properties.ManagedPolicyArns,
    ).toBeUndefined();

    const actions = Object.values(
      template.findResources("AWS::IAM::Policy", attachedToRuntimeRole),
    )
      .flatMap((policy) => policy.Properties.PolicyDocument.Statement)
      .filter((s) => s.Effect === "Allow")
      .flatMap((s) => [s.Action].flat());
    expect([...new Set(actions)].sort()).toEqual(
      [
        // 調査ツール用（読み取り専用）
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
        // モデル呼び出しと通知
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "sns:Publish",
        // AgentCore Runtime の L2 construct が既定で付ける（イメージ取得・自身のログ出力・
        // トレース・メトリクス・ワークロード ID。PutMetricData は bedrock-agentcore 名前空間に、
        // ログ出力は自身のロググループに限定されている）
        "bedrock-agentcore:GetWorkloadAccessToken",
        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
        "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
        "cloudwatch:PutMetricData",
        "ecr:BatchCheckLayerAvailability",
        "ecr:BatchGetImage",
        "ecr:GetAuthorizationToken",
        "ecr:GetDownloadUrlForLayer",
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "xray:GetSamplingRules",
        "xray:GetSamplingTargets",
        "xray:PutTelemetryRecords",
        "xray:PutTraceSegments",
      ].sort(),
    );

    const json = JSON.stringify(template.toJSON());
    // 書き込み系の権限を付与していないこと
    expect(json).not.toContain("cloudwatch:PutMetricAlarm");
    expect(json).not.toContain("logs:DeleteLogGroup");
  });

  test("中継 Lambda が Python 3.14・15 分タイムアウトで作成される", () => {
    template.hasResourceProperties("AWS::Lambda::Function", {
      Runtime: "python3.14",
      Handler: "handler.handler",
      Timeout: 900,
      // 関数名は README と Slack の案内に実名で載せるため、自動生成に任せず決め打ちにする
      FunctionName: "TestStack-invoker",
      Environment: {
        Variables: Match.objectLike({
          AGENT_RUNTIME_ARN: Match.anyValue(),
        }),
      },
    });
  });

  test("中継 Lambda のロググループはスタック削除時に一緒に消える", () => {
    // 残ると関数名が決め打ちのため、作り直しのデプロイが AlreadyExists で失敗する
    const invokerId = template.getResourceId("AWS::Lambda::Function", {
      Properties: { FunctionName: "TestStack-invoker" },
    });
    template.resourceCountIs("AWS::Logs::LogGroup", 1);
    template.hasResource("AWS::Logs::LogGroup", {
      Properties: Match.objectLike({
        LogGroupName: {
          "Fn::Join": ["", ["/aws/lambda/", { Ref: invokerId }]],
        },
      }),
      DeletionPolicy: "Delete",
      UpdateReplacePolicy: "Delete",
    });
  });

  test("エージェントに中継 Lambda の関数名が渡される", () => {
    // 依頼コマンドの案内を通知に実名で載せるために必要
    template.hasResourceProperties("AWS::BedrockAgentCore::Runtime", {
      EnvironmentVariables: Match.objectLike({
        INVOKER_FUNCTION_NAME: "TestStack-invoker",
        // Amazon Q Developer はリージョン未指定だと実行時に入力を求めてくる
        INVOKER_REGION: { Ref: "AWS::Region" },
      }),
    });
  });

  test("中継 Lambda の関数名が出力される", () => {
    template.hasOutput("InvokerFunctionName", { Value: "TestStack-invoker" });
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
    // Slack からのコマンド実行を許可する管理ポリシーも作られない
    template.resourceCountIs("AWS::IAM::ManagedPolicy", 0);
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
      // 未指定だと AdministratorAccess が既定になるため、必ず明示する
      GuardrailPolicies: Match.anyValue(),
    });
  });

  test("Slack から実行できるのは中継 Lambda の起動だけに絞られる", () => {
    const withSlack = synth({
      slackWorkspaceId: "T0123456789",
      slackChannelId: "C0123456789",
    });

    // ガードレール（管理ポリシー）とチャネルロールの両方で同じ範囲に絞る
    withSlack.hasResourceProperties("AWS::IAM::ManagedPolicy", {
      PolicyDocument: Match.objectLike({
        Statement: [
          Match.objectLike({
            Action: "lambda:InvokeFunction",
            Effect: "Allow",
            Resource: Match.anyValue(),
          }),
        ],
      }),
    });
    withSlack.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: Match.objectLike({
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: "lambda:InvokeFunction",
            Effect: "Allow",
            Resource: Match.anyValue(),
          }),
        ]),
      }),
      Roles: Match.anyValue(),
    });
  });

  test("調査期間の上限は parameter.ts で設定できる", () => {
    const custom = synth({ maxLookbackHours: 72 });
    custom.hasResourceProperties("AWS::BedrockAgentCore::Runtime", {
      EnvironmentVariables: Match.objectLike({ MAX_LOOKBACK_HOURS: "72" }),
    });
  });

  test("中継 Lambda のエラーを拾うアラームが SNS に通知する", () => {
    // エージェント側で捕捉できない失敗（コンテナ障害・タイムアウト）に気づくための網
    template.hasResourceProperties("AWS::CloudWatch::Alarm", {
      Namespace: "AWS/Lambda",
      MetricName: "Errors",
      Statistic: "Sum",
      Period: 300,
      EvaluationPeriods: 1,
      Threshold: 1,
      ComparisonOperator: "GreaterThanOrEqualToThreshold",
      // エラーが無い時間帯は「データなし」になるため、欠損を異常と見なさない
      TreatMissingData: "notBreaching",
      AlarmActions: Match.anyValue(),
    });
  });

  test("アラームは復旧時には通知しない", () => {
    // 翌日の日次通知が届けば復旧は分かるため、OK 遷移の通知は増やさない
    template.hasResourceProperties("AWS::CloudWatch::Alarm", {
      OKActions: Match.absent(),
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
