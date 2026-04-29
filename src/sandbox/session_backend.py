"""按 LangGraph 会话管理基础 BrowserSandbox 后端。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
import threading
from typing import Any

from agentscope_runtime.sandbox import BrowserSandbox
from langchain.tools import ToolRuntime

from sandbox.backend import AgentScopeDeepAgentsBackend

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_MOUNT_DIR = PROJECT_ROOT / "sessions_mount_dir"


class SessionSandboxManager:
    """为每个 LangGraph 线程创建并复用一个 BrowserSandbox 后端。"""

    def __init__(self) -> None:
        """初始化会话沙箱管理器。

        `_backends` 的 key 是 LangGraph 会话标识，value 是 DeepAgents 可直接使用的后端对象。
        后端内部持有底层 AgentScope BrowserSandbox，因此管理器不再单独保存 sandbox。
        """

        self._backends: dict[str, AgentScopeDeepAgentsBackend] = {}
        # LangGraph 可能并发执行多个工具调用，这里用锁保护会话沙箱的创建和关闭。
        self._lock = threading.Lock()

    def backend_for_runtime(self, runtime: ToolRuntime[Any, Any] | None = None) -> AgentScopeDeepAgentsBackend:
        """返回当前 LangGraph 线程对应的沙箱后端。

        第一次遇到某个 session_id 时创建新的 BrowserSandbox；
        后续同一线程再次调用工具时复用同一个沙箱，从而保留浏览器页面、文件和命令状态。
        """

        session_id = thread_id_from_runtime(runtime)
        with self._lock:
            backend = self._backends.get(session_id)
            if backend is None:
                backend = self._create_backend_for_session(session_id)
                self._backends[session_id] = backend
            return backend

    def _create_backend_for_session(self, session_id: str) -> AgentScopeDeepAgentsBackend:
        """创建并初始化当前会话的沙箱后端。"""

        try:
            return self._create_backend(session_id)
        except RuntimeError as exc:
            raise self._explain_sandbox_startup_error(exc) from exc

    def _create_backend(self, session_id: str) -> AgentScopeDeepAgentsBackend:
        """创建 BrowserSandbox，并包装成 DeepAgents 后端。"""

        # 传入 workspace_dir 会让 AgentScope 直接创建容器并挂载该目录，
        # 避免默认的 sandbox pool 路径在本地开发环境中没有预热容器时卡住。
        workspace_dir = session_workspace_dir(session_id)
        sandbox = BrowserSandbox(workspace_dir=str(workspace_dir))
        try:
            sandbox.__enter__()
            try:
                backend = AgentScopeDeepAgentsBackend.from_existing(
                    sandbox,
                    release_on_close=True,
                )
            except Exception:
                sandbox.__exit__(None, None, None)
                raise
            return backend
        except RuntimeError:
            raise

    def _explain_sandbox_startup_error(self, exc: RuntimeError) -> RuntimeError:
        """把 AgentScope 的泛化沙箱启动错误补充成项目可操作的提示。"""

        if "No sandbox available" not in str(exc):
            return exc

        return RuntimeError(
            "AgentScope BrowserSandbox 启动失败。常见原因是沙箱后端未启动、"
            "当前用户无权访问容器后端，或默认 BrowserSandbox 镜像不可用。"
            "如果使用 Docker 后端，请在服务器上确认 `docker ps` 可执行，"
            "并确认默认镜像 `agentscope/runtime-sandbox-browser:latest` 能被拉取和启动。"
            f"原始错误：{exc}"
        )

    def close(self) -> None:
        """关闭当前管理器维护的所有会话沙箱。"""

        with self._lock:
            backends = list(self._backends.values())
            self._backends.clear()

        # 退出锁之后再关闭后端，避免关闭底层沙箱时阻塞新的管理器操作。
        for backend in backends:
            backend.close()


def create_session_backend_factory(manager: SessionSandboxManager) -> Callable[[ToolRuntime[Any, Any]], AgentScopeDeepAgentsBackend]:
    """返回绑定到 SessionSandboxManager 的 DeepAgents 后端工厂。"""

    def factory(runtime: ToolRuntime[Any, Any]) -> AgentScopeDeepAgentsBackend:
        """DeepAgents 每次执行工具时会调用这个函数获取当前线程的后端。"""

        return manager.backend_for_runtime(runtime)

    return factory


def thread_id_from_runtime(runtime: object | None) -> str:
    """从 ToolRuntime 中提取 LangGraph 线程 id，用来隔离不同用户的沙箱。"""

    config = getattr(runtime, "config", None)
    candidates: list[dict[str, Any]] = []

    # LangGraph 会把线程信息放在 config 的不同层级；这里按常见位置依次收集。
    if isinstance(config, dict):
        for key in ("configurable", "metadata"):
            value = config.get(key, {})
            if isinstance(value, dict):
                candidates.append(value)
        candidates.append(config)

    # 部分运行环境也可能把线程信息放在 runtime.context。
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        candidates.append(context)

    # thread_id 是最稳定的会话标识，优先使用。
    for candidate in candidates:
        for key in ("thread_id", "langgraph_thread_id"):
            value = candidate.get(key)
            if value:
                return str(value)
    # 找不到 thread_id 时，用检查点或运行 id 兜底，至少避免所有请求落到同一个沙箱。
    for candidate in candidates:
        for key in ("checkpoint_ns", "checkpoint_id", "run_id"):
            value = candidate.get(key)
            if value:
                return str(value)
    return "default"


def session_workspace_dir(session_id: str) -> Path:
    """返回当前 LangGraph 会话对应的宿主机挂载目录。"""

    safe_session_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id).strip("._")
    if not safe_session_id:
        safe_session_id = "default"

    workspace_dir = SESSIONS_MOUNT_DIR / safe_session_id
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir
