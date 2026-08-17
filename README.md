# Welcome to your CDK TypeScript project

This is a blank project for CDK development with TypeScript.

The `cdk.json` file tells the CDK Toolkit how to execute your app.

## Setup

This project uses [mise](https://mise.jdx.dev/) to manage Node.js and pnpm versions (see `mise.toml`).

```sh
mise install
pnpm install
```

## Useful commands

* `pnpm build`      type-check the project
* `pnpm watch`      watch for changes and type-check
* `pnpm test`       perform the jest unit tests
* `pnpm cdk deploy` deploy this stack to your default AWS account/region
* `pnpm cdk diff`   compare deployed stack with current state
* `pnpm cdk synth`  emits the synthesized CloudFormation template
