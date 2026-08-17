import type { OpsAgentParameters } from "./stacks/ops-agent-stack";

/**
 * デプロイパラメータのサンプル。
 * このファイルを parameter.ts にコピーして、環境に合わせて編集する。
 *
 *   cp parameter.sample.ts parameter.ts
 *
 * parameter.ts は .gitignore 済みなので、個人環境の値がコミットされることはない。
 */
export const parameters: OpsAgentParameters = {
  scheduleCron: "cron(0 8 * * ? *)",
  scheduleTimeZone: "Asia/Tokyo",
  modelId: "jp.anthropic.claude-sonnet-4-6",
  scoreThreshold: 50,
  lookbackHours: 24,
  targetRegions: [],
  // Slack 連携を使う場合はコメントを外して設定する
  // slackWorkspaceId: "TXXXXXXXX",
  // slackChannelId: "CXXXXXXXX",
};
