#!/usr/bin/env node
import * as cdk from "aws-cdk-lib/core";
import { parameters } from "./parameter";
import { OpsAgentStack } from "./stacks/ops-agent-stack";

const app = new cdk.App();
new OpsAgentStack(app, "OpsAgentSampleOnAwsStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  parameters,
});
