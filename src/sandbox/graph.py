"""LangGraph 部署入口：导出可由 langgraph.json 加载的真实 DeepAgent。"""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import sys
from typing import Any


def log(message: str) -> None:
    """把 graph 加载阶段的关键信息打印到 LangGraph dev 控制台。"""

    print(f"[sandbox] {message}", file=sys.stderr, flush=True)


def load_env_file(path: Path = Path(".env")) -> None:
    """加载简单的 .env 文件；部署时也可交给 langgraph.json 的 env 字段加载。"""

    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()
os.environ.setdefault("CONTAINER_DEPLOYMENT", "docker")

# 这些导入依赖上面的环境变量，所以必须放在环境初始化之后。
from deepagents import create_deep_agent  # noqa: E402

from sandbox.diagnostics import DiagnosticsMiddleware  # noqa: E402
from sandbox.session_backend import (  # noqa: E402
    SessionSandboxManager,
    create_session_backend_factory,
)

_SANDBOX_MANAGER: SessionSandboxManager | None = None


def get_model_name() -> str:
    """读取真实模型名称；部署时不回退到假模型。"""

    model_name = os.environ.get("DEEPAGENTS_MODEL", "").strip()
    if not model_name:
        raise RuntimeError("请先在 .env 中填写 DEEPAGENTS_MODEL。")
    return model_name


def validate_model_config() -> None:
    """校验真实模型配置，避免 DeepAgents 运行时回退到不支持工具的假模型。"""

    model_name = get_model_name()
    if model_name.startswith("openai:") and not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RuntimeError("DEEPAGENTS_MODEL 使用 openai: 前缀时，请先在 .env 中填写 OPENAI_API_KEY。")


def get_sandbox_manager() -> SessionSandboxManager:
    """创建并复用按 LangGraph 会话管理 AgentScope 沙箱的管理器。"""

    global _SANDBOX_MANAGER

    if _SANDBOX_MANAGER is None:
        _SANDBOX_MANAGER = SessionSandboxManager()
    return _SANDBOX_MANAGER


def close_backend() -> None:
    """进程退出时关闭所有 AgentScope 会话沙箱。"""

    global _SANDBOX_MANAGER

    if _SANDBOX_MANAGER is not None:
        _SANDBOX_MANAGER.close()
    _SANDBOX_MANAGER = None


def create_graph() -> Any:
    """构建 DeepAgent graph，供 LangGraph 部署使用。"""

    validate_model_config()
    log(f"创建 LangGraph agent：model={get_model_name()} deployment={os.environ.get('CONTAINER_DEPLOYMENT')}")
    sandbox_manager = get_sandbox_manager()
    # 这里传给 DeepAgents 的不是单个后端，而是一个工厂函数。
    # DeepAgents 执行工具时会把 ToolRuntime 传进来，工厂函数再据此选择当前线程的沙箱。
    backend = create_session_backend_factory(sandbox_manager)

    return create_deep_agent(
        model=get_model_name(),
        backend=backend,
        middleware=[DiagnosticsMiddleware()],
    )


# langgraph.json 直接加载这个变量：./src/sandbox/graph.py:agent
agent = create_graph()

# LangGraph 进程退出时关闭所有还在运行的会话沙箱。
atexit.register(close_backend)
