#!/usr/bin/env bash
set -euo pipefail

# LangGraph dev 启动脚本。
# AgentScope Runtime 当前通过同步 requests 调用沙箱 HTTP 服务，
# langgraph dev 默认会用 blockbuster 检测并阻止这类调用；
# --allow-blocking 用于开发环境，BG_JOB_ISOLATED_LOOPS 用于更接近部署的后台任务隔离。

export BG_JOB_ISOLATED_LOOPS="${BG_JOB_ISOLATED_LOOPS:-true}"
export CONTAINER_DEPLOYMENT="${CONTAINER_DEPLOYMENT:-docker}"
# LangGraph 按文件路径加载 src/sandbox/graph.py。
# 显式加入 src，避免本地 editable 安装的 .pth 文件未生效时导入失败。
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}src"

SKIP_DOCKER_CHECK=false
for arg in "$@"; do
  if [[ "$arg" == "--help" || "$arg" == "-h" ]]; then
    SKIP_DOCKER_CHECK=true
    break
  fi
done

if [[ "$CONTAINER_DEPLOYMENT" == "docker" && "$SKIP_DOCKER_CHECK" == "false" ]]; then
  if ! command -v docker >/dev/null 2>&1; then
    cat >&2 <<'EOF'
当前配置使用 CONTAINER_DEPLOYMENT=docker，但没有找到 docker 命令。

基础 BrowserSandbox 仍然需要一个容器后端。请先安装并启动 Docker Desktop，
或者使用 Colima 并设置：

  export DOCKER_HOST=unix://$HOME/.colima/docker.sock

然后确认下面命令可用：

  docker ps
EOF
    exit 1
  fi

  if ! docker ps >/dev/null 2>&1; then
    cat >&2 <<'EOF'
当前配置使用 CONTAINER_DEPLOYMENT=docker，但无法连接 Docker daemon。

请确认 Docker Desktop/Colima 已启动，并且当前用户可以执行：

  docker ps

如果使用 Colima，通常需要：

  export DOCKER_HOST=unix://$HOME/.colima/docker.sock
EOF
    exit 1
  fi
fi

uv run langgraph dev --allow-blocking --no-reload "$@"
