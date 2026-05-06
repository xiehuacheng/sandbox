"""按 LangGraph 会话管理基础 BrowserSandbox 后端。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
import sys
import threading
from typing import Any

from agentscope_runtime.sandbox.enums import SandboxType
from agentscope_runtime.sandbox.manager import SandboxManager
from agentscope_runtime.sandbox.model import ContainerState
from agentscope_runtime.sandbox.manager.server.app import get_config
from sandbox.backend import AgentScopeDeepAgentsBackend

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SESSIONS_MOUNT_DIR = PROJECT_ROOT / "sessions_mount_dir"
PROJECT_SKILLS_DIR = PROJECT_ROOT / "skills" / "project"
SANDBOX_PROJECT_SKILLS_DIR = "/workspace/skills/project"


def log(message: str) -> None:
    """把关键诊断信息打印到 LangGraph dev 控制台。"""

    print(f"[sandbox] {message}", file=sys.stderr, flush=True)


class SessionSandboxManager:
    """把 LangGraph 线程映射到 DeepAgents 可用的 AgentScope 后端。

    底层容器生命周期交给 AgentScope `SandboxManager`：创建、释放、
    heartbeat、session_mapping、restore 和 backend deployment 都由它负责。
    这里仅保留 LangGraph thread_id 到 DeepAgents backend 的胶水逻辑。
    """

    def __init__(self) -> None:
        """初始化会话沙箱管理器。

        `_backends` 的 key 是 LangGraph 会话标识，value 是 DeepAgents 可直接使用的后端对象。
        后端内部持有已绑定 sandbox_id 的 AgentScope BrowserSandbox。
        """

        self._backends: dict[str, AgentScopeDeepAgentsBackend] = {}
        self._sandbox_manager: SandboxManager | None = None
        # LangGraph 可能并发执行多个工具调用，这里用锁保护会话沙箱的创建和关闭。
        self._lock = threading.Lock()

    def backend_for_runtime(self, runtime: object | None = None) -> AgentScopeDeepAgentsBackend:
        """返回当前 LangGraph 线程对应的沙箱后端。

        第一次遇到某个 session_id 时创建新的 BrowserSandbox；
        后续同一线程再次调用工具时复用同一个沙箱，从而保留浏览器页面、文件和命令状态。
        """

        session_id = thread_id_from_runtime(runtime)
        log(f"获取会话后端：session_id={session_id}")
        return self.backend_for_session_id(session_id)

    def backend_for_session_id(self, session_id: str) -> AgentScopeDeepAgentsBackend:
        """返回指定会话 id 对应的沙箱后端。

        这个方法给应用层 HTTP API 使用。上传文件、下载文件或注入
        chrome-devtools 这类动作没有 DeepAgents ToolRuntime，只能由业务系统
        按 LangGraph thread_id 主动定位同一个会话沙箱。
        """

        requested_id = str(session_id)
        session_id = normalize_session_id(requested_id)
        with self._lock:
            backend = self._backends.get(session_id)
            if backend is None:
                backend = self._backend_by_sandbox_id(requested_id)
            if backend is None and requested_id != session_id:
                backend = self._backend_by_sandbox_id(session_id)
            if backend is None:
                backend = self._backend_from_agent_scope_session(session_id)
            if backend is None:
                log(f"当前会话还没有沙箱，准备创建：session_id={session_id}")
                backend = self._create_backend_for_session(session_id)
                self._backends[session_id] = backend
            else:
                log(f"复用当前会话已有沙箱：session_id={session_id}")
                self._backends[session_id] = backend
            return backend

    def inject_chrome_devtools(
        self,
        session_id: str,
        *,
        package: str | None = None,
        force: bool = False,
    ) -> AgentScopeDeepAgentsBackend:
        """给指定会话沙箱注入 chrome-devtools CLI，并返回该后端。"""

        backend = self.backend_for_session_id(session_id)
        log(f"注入 chrome-devtools CLI：session_id={normalize_session_id(session_id)}")
        result = backend.inject_chrome_devtools(package=package, force=force)
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "chrome-devtools 注入失败。")
        return backend

    def inject_npm_cli(
        self,
        session_id: str,
        *,
        package: str,
        commands: list[str],
        force: bool = False,
        install_name: str | None = None,
    ) -> AgentScopeDeepAgentsBackend:
        """给指定会话沙箱注入 npm CLI 命令，并返回该后端。"""

        backend = self.backend_for_session_id(session_id)
        log(
            "注入 npm CLI："
            f"session_id={normalize_session_id(session_id)} package={package} commands={commands}",
        )
        result = backend.inject_npm_cli(
            package=package,
            commands=commands,
            force=force,
            install_name=install_name,
        )
        if result.exit_code not in (0, None):
            raise RuntimeError(result.output.strip() or "npm CLI 注入失败。")
        return backend

    def _backend_by_sandbox_id(self, sandbox_id: str) -> AgentScopeDeepAgentsBackend | None:
        """按底层 AgentScope sandbox_id 查找已创建的后端。"""

        for backend in self._backends.values():
            try:
                if backend.id == sandbox_id:
                    return backend
            except RuntimeError:
                continue
        return None

    def _backend_from_agent_scope_session(self, session_id: str) -> AgentScopeDeepAgentsBackend | None:
        """从 AgentScope session mapping 里绑定已有会话沙箱。"""

        sandbox_manager = self._agent_scope_manager()
        try:
            if sandbox_manager.needs_restore(session_id):
                log(f"AgentScope 会话需要恢复，准备 restore：session_id={session_id}")
                sandbox_manager.restore_session(session_id)

            sandbox_ids = sandbox_manager.get_session_mapping(session_id)
        except Exception as exc:  # noqa: BLE001
            log(f"读取 AgentScope session mapping 失败：session_id={session_id} error={exc}")
            return None

        for sandbox_id in sandbox_ids:
            try:
                info = sandbox_manager.get_info(sandbox_id)
                state = info.get("state") if isinstance(info, dict) else None
                if state not in (ContainerState.RUNNING, ContainerState.RUNNING.value, None):
                    continue
                sandbox_manager.update_heartbeat(session_id)
                log(f"绑定 AgentScope 已有会话沙箱：session_id={session_id} sandbox_id={sandbox_id}")
                return AgentScopeDeepAgentsBackend.from_existing(
                    ManagedBrowserSandbox(sandbox_manager, sandbox_id),
                    release_on_close=False,
                )
            except Exception as exc:  # noqa: BLE001
                log(f"跳过不可用 AgentScope 沙箱：session_id={session_id} sandbox_id={sandbox_id} error={exc}")
        return None

    def _create_backend_for_session(self, session_id: str) -> AgentScopeDeepAgentsBackend:
        """创建并初始化当前会话的沙箱后端。"""

        try:
            return self._create_backend(session_id)
        except RuntimeError as exc:
            raise self._explain_sandbox_startup_error(exc) from exc

    def _create_backend(self, session_id: str) -> AgentScopeDeepAgentsBackend:
        """创建 BrowserSandbox，并包装成 DeepAgents 后端。"""

        workspace_dir = session_workspace_dir(session_id)
        log(f"创建 BrowserSandbox：session_id={session_id} workspace_dir={workspace_dir}")
        sandbox_manager = self._agent_scope_manager()
        sandbox_id = sandbox_manager.create(
            sandbox_type=SandboxType.BROWSER.value,
            mount_dir=str(workspace_dir),
            meta={"session_ctx_id": session_id},
        )
        if not sandbox_id:
            raise RuntimeError("No sandbox available.")

        log(f"BrowserSandbox 启动成功：sandbox_id={sandbox_id}")
        sandbox = ManagedBrowserSandbox(sandbox_manager, sandbox_id)
        backend = AgentScopeDeepAgentsBackend.from_existing(
            sandbox,
            release_on_close=False,
        )
        try:
            self._upload_project_skills(backend)
        except Exception:
            sandbox_manager.release(sandbox_id)
            raise
        return backend

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

    def _upload_project_skills(self, backend: AgentScopeDeepAgentsBackend) -> None:
        """把项目 skill 说明上传到当前沙箱。"""

        if not PROJECT_SKILLS_DIR.exists():
            return

        files: list[tuple[str, bytes]] = []
        for path in PROJECT_SKILLS_DIR.rglob("*"):
            if path.is_file():
                relative_path = path.relative_to(PROJECT_SKILLS_DIR).as_posix()
                sandbox_path = f"{SANDBOX_PROJECT_SKILLS_DIR}/{relative_path}"
                files.append((sandbox_path, path.read_bytes()))

        if not files:
            return

        responses = backend.upload_files(files)
        errors = [response for response in responses if response.error]
        if errors:
            first = errors[0]
            raise RuntimeError(
                f"上传项目 skills 失败：{first.path}: {first.error}",
            )
        log(f"已上传项目 skills：count={len(files)} path={SANDBOX_PROJECT_SKILLS_DIR}")

    def close(self) -> None:
        """关闭当前管理器维护的所有会话沙箱。"""

        with self._lock:
            backends = list(self._backends.values())
            self._backends.clear()

        sandbox_manager = self._sandbox_manager
        if sandbox_manager is None:
            log("已关闭会话沙箱数量：0")
            return

        # 退出锁之后再关闭后端，避免关闭底层沙箱时阻塞新的管理器操作。
        for backend in backends:
            sandbox_id = "<unknown>"
            try:
                sandbox_id = backend.id
                sandbox_manager.release(sandbox_id)
            except Exception as exc:  # noqa: BLE001
                log(f"关闭会话沙箱失败：sandbox_id={sandbox_id} error={exc}")
        sandbox_manager.__exit__(None, None, None)
        self._sandbox_manager = None
        log(f"已关闭会话沙箱数量：{len(backends)}")

    def _agent_scope_manager(self) -> SandboxManager:
        """按需创建共享的 AgentScope SandboxManager。"""

        if self._sandbox_manager is None:
            self._sandbox_manager = self._create_agent_scope_manager()
        return self._sandbox_manager

    def _create_agent_scope_manager(self) -> SandboxManager:
        """创建共享的 AgentScope SandboxManager。

        AgentScope 的 manager 负责 Docker/BoxLite/K8s 等后端选择、
        容器生命周期、session_ctx_id 映射、heartbeat 和清理。
        """

        config = get_config()
        config.allow_mount_dir = True
        manager = SandboxManager(
            config=config,
            default_type=SandboxType.BROWSER,
        )
        manager.__enter__()
        log(
            "已启动 AgentScope SandboxManager："
            f"deployment={config.container_deployment} heartbeat_timeout={config.heartbeat_timeout}",
        )
        return manager


class ManagedBrowserSandbox:
    """共享 AgentScope SandboxManager 上的 BrowserSandbox 轻量句柄。"""

    def __init__(self, manager: SandboxManager, sandbox_id: str) -> None:
        self.manager = manager
        self._sandbox_id = sandbox_id

    @property
    def sandbox_id(self) -> str:
        return self._sandbox_id

    def run_shell_command(self, command: str) -> Any:
        return self.manager.call_tool(
            self._sandbox_id,
            "run_shell_command",
            {"command": command},
        )

    def close(self) -> None:
        self.manager.release(self._sandbox_id)


def create_session_backend_factory(manager: SessionSandboxManager) -> Callable[[object], AgentScopeDeepAgentsBackend]:
    """返回绑定到 SessionSandboxManager 的 DeepAgents 后端工厂。"""

    def factory(runtime: object) -> AgentScopeDeepAgentsBackend:
        """DeepAgents 每次执行工具时会调用这个函数获取当前线程的后端。"""

        return manager.backend_for_runtime(runtime)

    return factory


def thread_id_from_runtime(runtime: object | None) -> str:
    """从 ToolRuntime 中提取 LangGraph 线程 id，用来隔离不同用户的沙箱。"""

    # DeepAgents 在模型调用前解析 backend 时传入的是 LangGraph Runtime；
    # 这个对象没有 config，线程信息放在 execution_info.thread_id。
    execution_info = getattr(runtime, "execution_info", None)
    if execution_info is not None:
        for key in ("thread_id", "run_id", "checkpoint_id"):
            value = getattr(execution_info, key, None)
            if value:
                return str(value)

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

    safe_session_id = normalize_session_id(session_id)

    workspace_dir = SESSIONS_MOUNT_DIR / safe_session_id
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return workspace_dir


def normalize_session_id(session_id: str) -> str:
    """把外部传入的 thread/session id 规范成安全目录名。"""

    safe_session_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(session_id)).strip("._")
    if not safe_session_id:
        safe_session_id = "default"
    return safe_session_id
