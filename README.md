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

5. `src/sandbox/diagnostics.py`

   运行时诊断日志。它不参与业务逻辑，只帮助判断请求是否进入 agent、模型节点和沙箱后端。

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
│   ├── diagnostics.py
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
DeepAgents 准备模型请求或执行沙箱工具时调用 session_backend.py 里的后端工厂
  ↓
session_backend.py 优先从 runtime.execution_info.thread_id 里取 LangGraph thread_id
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

`.env` 至少需要配置真实模型。模型名称遵循 LangChain `init_chat_model` 支持的格式；如果使用 OpenAI 官方模型，建议显式带上 `openai:` 前缀：

```bash
DEEPAGENTS_MODEL=openai:gpt-5.4-mini
OPENAI_API_KEY=你的 Key
# OPENAI_BASE_URL=https://你的 OpenAI 兼容网关/v1
```

沙箱后端按部署环境配置。示例使用 Docker：

```bash
CONTAINER_DEPLOYMENT=docker
BG_JOB_ISOLATED_LOOPS=true
RUNTIME_SANDBOX_REGISTRY=agentscope-registry.ap-southeast-1.cr.aliyuncs.com
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

服务器上如果要从外部访问，可以监听 `0.0.0.0`：

```bash
./run_langgraph_dev.sh --host 0.0.0.0 --port 2024
```

LangGraph dev 打印的 Studio URL 里通常会带 `baseUrl=http://127.0.0.1:2024`。如果浏览器不在服务器本机，需要二选一：

1. 用 SSH 端口转发，让本机的 `127.0.0.1:2024` 指到服务器：

   ```bash
   ssh -L 2024:127.0.0.1:2024 用户名@服务器地址
   ```

2. 或者把 Studio URL 里的 `baseUrl` 改成服务器可访问地址，例如：

   ```text
   https://smith.langchain.com/studio/?baseUrl=http://服务器IP:2024
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

## 测试 Prompt

启动后在 Studio 新建 thread，发送：

```text
请必须调用 execute 工具执行：pwd && ls -la /workspace
不要自己回答。只返回 execute 工具的原始输出。
```

正常情况下，LangGraph 控制台会看到类似日志：

```text
[sandbox] 进入 agent run
[sandbox] 准备调用模型
[sandbox] 获取会话后端：session_id=019...
[sandbox] 创建 BrowserSandbox
[sandbox] BrowserSandbox 启动成功：sandbox_id=runtime_sandbox_container_...
```

同时 `docker ps` 能看到 `runtime_sandbox_container_...` 容器。

## 运行数据

项目运行时会产生两类本地数据：

```text
.langgraph_api/
sessions_mount_dir/
```

`.langgraph_api/` 是 LangGraph dev 的本地持久化数据，包含 dev 模式下的线程、run 队列等状态。

`sessions_mount_dir/<thread_id>` 是每个 LangGraph thread 挂载到 BrowserSandbox 的工作目录。这个目录会保留同一会话里的文件状态。

这两个目录都已经加入 `.gitignore`。

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
./run_langgraph_dev.sh --host 0.0.0.0 --port 2024
```

`Docker client initialization failed: Error while fetching server API version`：当前配置使用 `CONTAINER_DEPLOYMENT=docker`，但 LangGraph 进程连不上 Docker daemon。先确认本机有 `docker` 命令并且 `docker ps` 可以执行；如果使用 Colima，设置 `DOCKER_HOST=unix://$HOME/.colima/docker.sock`。

`Required package 'langgraph-api' is not installed` 且提示 Python 3.9：通常是项目改名或移动后，`.venv/bin/langgraph` 的入口脚本还指向旧虚拟环境。执行下面命令重新生成入口脚本：

```bash
uv sync --reinstall-package langgraph-cli
```

`Queue stats` 里一直有 `n_running=1`、`n_pending` 不断增加：通常是旧 run 卡住了，新的请求都在排队。开发环境可以停止服务并清理本地 dev 状态：

```bash
pkill -f "langgraph dev" || true
rm -rf .langgraph_api
./run_langgraph_dev.sh --host 0.0.0.0 --port 2024
```

清理 `.langgraph_api` 后，Studio 里的旧 thread 会失效。如果日志里出现：

```text
GET /threads/<旧 thread_id> 404
POST /threads/<旧 thread_id>/history 404
```

说明 Studio 还在访问旧 thread。刷新 Studio 后新建 thread；如果仍然自动跳旧会话，可以用无痕窗口打开 Studio，或清理 `smith.langchain.com` 的浏览器 localStorage。

只看到 `[sandbox] 创建 LangGraph agent`：这只表示 graph 被 LangGraph 加载，还不代表用户请求已经进入 agent。正常请求进入后，还应该看到 `[sandbox] 进入 agent run`。如果没有，优先检查 Studio 的 `baseUrl`、thread 是否是新建的，以及队列是否被旧 run 卡住。

`session_id=default`：说明运行时没有拿到 LangGraph thread id。当前代码会优先读取 `runtime.execution_info.thread_id`；如果仍出现 `default`，先确认服务器代码已经更新到包含 `Use LangGraph execution thread id` 的版本，并完全重启 LangGraph dev。
