"""进程内沙箱运行时控制入口。"""

from __future__ import annotations

from sandbox.session_backend import SessionSandboxManager

_SANDBOX_MANAGER: SessionSandboxManager | None = None


def get_sandbox_manager() -> SessionSandboxManager:
    """创建并复用按 LangGraph 会话管理 AgentScope 沙箱的管理器。"""

    global _SANDBOX_MANAGER

    if _SANDBOX_MANAGER is None:
        _SANDBOX_MANAGER = SessionSandboxManager()
    return _SANDBOX_MANAGER


def inject_chrome_devtools(
    session_id: str,
    *,
    package: str | None = None,
    force: bool = False,
) -> str:
    """给指定 LangGraph thread/session 对应的沙箱显式注入 chrome-devtools CLI。"""

    backend = get_sandbox_manager().inject_chrome_devtools(
        session_id,
        package=package,
        force=force,
    )
    return backend.id


def inject_npm_cli(
    session_id: str,
    *,
    package: str,
    commands: list[str],
    force: bool = False,
    install_name: str | None = None,
) -> str:
    """给指定 LangGraph thread/session 对应的沙箱显式注入 npm CLI 命令。"""

    backend = get_sandbox_manager().inject_npm_cli(
        session_id,
        package=package,
        commands=commands,
        force=force,
        install_name=install_name,
    )
    return backend.id


def close_backend() -> None:
    """进程退出时关闭所有 AgentScope 会话沙箱。"""

    global _SANDBOX_MANAGER

    if _SANDBOX_MANAGER is not None:
        _SANDBOX_MANAGER.close()
    _SANDBOX_MANAGER = None
