# AgentCore Runtime 用イメージ（linux/arm64 でビルドされる前提。CDK 側で platform を指定）
FROM public.ecr.aws/docker/library/python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /usr/local/bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# 依存関係のインストールをレイヤーキャッシュできるよう、先にロックファイルだけコピーする
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# AgentCore Runtime は 8080 番ポートの /invocations (POST) と /ping (GET) を呼び出す
EXPOSE 8080
CMD ["python", "-m", "ops_agent.main"]
