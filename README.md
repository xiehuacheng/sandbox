# Sandbox

这个项目演示如何把 AgentScope Runtime 的基础 `BrowserSandbox` 接入 DeepAgents 的 `create_deep_agent`。

当前版本不再集成 `chrome-devtools` CLI，不注册自定义 BrowserSandbox 镜像，也不构建自定义 Docker 镜像。agent 只通过 DeepAgents 内置沙箱工具使用基础沙箱能力，例如 `execute`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`。

## 先读哪里

建议按这个顺序读：

1. `langgraph.json`

   LangGraph 入口配置，加载 `src/sandbox/graph.py:agent`。

2. `src/sandbox/graph.py`

   agent 组装入口。它读取环境变量，创建 `SessionSandboxManager`，然后把沙箱后端工厂传给 `create_deep_agent`。

3. `src/sandbox/session_backend.py`

   多会话隔离核心。它按 LangGraph `thread_id` 为每个会话创建并复用独立的基础 `BrowserSandbox`。

4. `src/sandbox/backend.py`

   DeepAgents 和 AgentScope Runtime 的适配层，把 AgentScope 沙箱包装成 DeepAgents 可用的 backend。

## 项目结构

```text
.
├── langgraph.json
├── run_langgraph_dev.sh
├── pyproject.toml
├── src/sandbox/
│   ├── graph.py
│   ├── session_backend.py
│   ├── backend.py
│   └── __init__.py
└── skills/project/
    ├── chrome-devtools/SKILL.md
    └── runpython/SKILL.md
```

`skills/` 目前保留为项目参考材料，不会自动传给 `create_deep_agent`。如果后续确定沙箱镜像里具备对应命令，再把需要的 skills 显式接入即可。

`.env` 是本地环境变量文件，`.venv/` 是本地 Python 虚拟环境，`src/*.egg-info/`、`__pycache__/`、`.langgraph_api/`、`sessions_mount_dir/` 都是运行或安装过程产生的生成物，不属于业务代码；这些路径已经写进 `.gitignore`。

## 核心流程

```text
用户请求
  ↓
LangGraph 根据 langgraph.json 加载 graph.py:agent
  ↓
graph.py 创建 DeepAgent
  ↓
DeepAgents 执行沙箱工具时调用 session_backend.py 里的后端工厂
  ↓
session_backend.py 从 ToolRuntime 里取 thread_id
  ↓
同一个 thread_id 复用同一个 BrowserSandbox；新的 thread_id 创建新沙箱
  ↓
每个 session 会挂载到本地 sessions_mount_dir/<thread_id>
  ↓
backend.py 把 DeepAgents 的 execute/read/write 等操作转成 AgentScope 沙箱命令
```

## 环境要求

服务器需要：

- Python 3.11+
- `uv`
- 可用的 AgentScope Runtime 沙箱后端

如果使用 Docker 作为 AgentScope Runtime 后端，需要保证当前运行 LangGraph 的用户可以执行：

```bash
docker ps
```

如果本机还没有 `docker` 命令，需要先安装并启动 Docker Desktop。使用 Colima 时，通常需要先启动 Colima 并导出 Docker socket：

```bash
colima start
export DOCKER_HOST=unix://$HOME/.colima/docker.sock
docker ps
```

## 安装

```bash
uv sync
```

## 环境变量

`.env` 至少需要配置真实模型：

```bash
DEEPAGENTS_MODEL=openai:gpt-5-mini
OPENAI_API_KEY=你的 Key
# OPENAI_BASE_URL=https://你的 OpenAI 兼容网关/v1
```

沙箱后端按部署环境配置。示例使用 Docker：

```bash
CONTAINER_DEPLOYMENT=docker
BG_JOB_ISOLATED_LOOPS=true
```

## 启动

开发启动：

```bash
./run_langgraph_dev.sh
```

端口被占用时：

```bash
./run_langgraph_dev.sh --port 2025
```

## 沙箱能力

agent 通过 DeepAgents 内置工具面访问沙箱：

```text
execute
ls/read_file/write_file/edit_file/glob/grep
task
write_todos
```

这里的 `execute` 会进入当前会话对应的 AgentScope `BrowserSandbox` 执行 shell 命令。

## 常见问题

`Blocking call to socket.socket.connect`：开发环境使用 `./run_langgraph_dev.sh`，它会加 `--allow-blocking --no-reload`。

`No sandbox available`：这是 AgentScope 的泛化错误。常见原因是沙箱后端未启动、容器后端无权限、默认 BrowserSandbox 镜像不可用，或沙箱资源耗尽。

当前项目会给每个 LangGraph `thread_id` 传入独立的 `workspace_dir`，路径在：

```text
sessions_mount_dir/<thread_id>
```

这样 AgentScope 会直接创建容器并挂载这个目录，不依赖预热的 sandbox pool。这个目录是运行数据，已经被 `.gitignore` 忽略。

服务器上可以先单独检查默认 BrowserSandbox 镜像：

```bash
docker ps
docker pull agentscope/runtime-sandbox-browser:latest
docker run --rm -d --name sandbox-browser-check agentscope/runtime-sandbox-browser:latest
docker logs sandbox-browser-check
docker rm -f sandbox-browser-check
```

如果 Docker Hub 拉取慢或失败，可以改用 AgentScope 官方镜像仓库：

```bash
export RUNTIME_SANDBOX_REGISTRY=agentscope-registry.ap-southeast-1.cr.aliyuncs.com
./run_langgraph_dev.sh --host 127.0.0.1 --port 2024
```

`Docker client initialization failed: Error while fetching server API version`：当前配置使用 `CONTAINER_DEPLOYMENT=docker`，但 LangGraph 进程连不上 Docker daemon。先确认本机有 `docker` 命令并且 `docker ps` 可以执行；如果使用 Colima，设置 `DOCKER_HOST=unix://$HOME/.colima/docker.sock`。

`Required package 'langgraph-api' is not installed` 且提示 Python 3.9：通常是项目改名或移动后，`.venv/bin/langgraph` 的入口脚本还指向旧虚拟环境。执行下面命令重新生成入口脚本：

```bash
uv sync --reinstall-package langgraph-cli
```
