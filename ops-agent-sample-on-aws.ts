#!/usr/bin/env node
import * as cdk from "aws-cdk-lib/core";
import { parameters } from "./parameter";
import { createOpsAgentStack } from "./stacks/ops-agent-stack";

const app = new cdk.App();
createOpsAgentStack(app, "OpsAgentOnAwsStack", {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION,
  },
  parameters,
});
