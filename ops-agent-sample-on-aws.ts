#!/usr/bin/env node
import * as cdk from "aws-cdk-lib/core";

const app = new cdk.App();
const stack = new cdk.Stack(app, "OpsAgentSampleOnAwsStack", {});
