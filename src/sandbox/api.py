"""Sandbox runtime control HTTP API."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from sandbox.runtime import inject_chrome_devtools, inject_npm_cli


class ChromeDevToolsInjectionRequest(BaseModel):
    """Options for injecting the chrome-devtools CLI into a session sandbox."""

    package: str | None = Field(default=None, description="npm package spec")
    force: bool = Field(default=False, description="reinstall even if wrappers exist")


class NpmCliInjectionRequest(BaseModel):
    """Options for injecting a generic npm CLI package into a session sandbox."""

    package: str = Field(description="npm package spec, for example some-cli@1.2.3")
    commands: list[str] = Field(
        min_length=1,
        description="CLI commands to expose in /workspace/.local/bin",
    )
    force: bool = Field(default=False, description="reinstall even if wrappers exist")
    install_name: str | None = Field(
        default=None,
        description="directory name under /workspace/.npm-tools",
    )


class ToolInjectionResponse(BaseModel):
    """Result returned after a tool injection request."""

    sandbox_id: str
    thread_id: str
    tool: str
    injected: bool = True


router = APIRouter(prefix="/sandbox", tags=["sandbox-tools"])


@router.post(
    "/sessions/{thread_id}/tools/chrome-devtools",
    response_model=ToolInjectionResponse,
)
def enable_chrome_devtools(
    thread_id: str,
    payload: ChromeDevToolsInjectionRequest | None = None,
) -> ToolInjectionResponse:
    """Explicitly inject chrome-devtools into the sandbox for one LangGraph thread."""

    payload = payload or ChromeDevToolsInjectionRequest()
    try:
        thread_id = _normalize_required_string(thread_id, "thread_id")
        sandbox_id = inject_chrome_devtools(
            thread_id,
            package=payload.package,
            force=payload.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ToolInjectionResponse(
        sandbox_id=sandbox_id,
        thread_id=thread_id,
        tool="chrome-devtools",
    )


@router.post(
    "/sessions/{thread_id}/tools/npm-cli",
    response_model=ToolInjectionResponse,
)
def enable_npm_cli(
    thread_id: str,
    payload: NpmCliInjectionRequest,
) -> ToolInjectionResponse:
    """Explicitly inject a generic npm CLI package into one session sandbox."""

    try:
        thread_id = _normalize_required_string(thread_id, "thread_id")
        package = _normalize_required_string(payload.package, "package")
        commands = _normalize_commands(payload.commands)
        sandbox_id = inject_npm_cli(
            thread_id,
            package=package,
            commands=commands,
            force=payload.force,
            install_name=payload.install_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ToolInjectionResponse(
        sandbox_id=sandbox_id,
        thread_id=thread_id,
        tool="npm-cli",
    )


def _normalize_commands(commands: list[str]) -> list[str]:
    """Reject empty command names before generating shell wrappers."""

    normalized = [command.strip() for command in commands]
    if any(not command for command in normalized):
        raise ValueError("commands 不能包含空字符串。")
    return normalized


def _normalize_required_string(value: str, field_name: str) -> str:
    """Normalize a required text field from an API payload."""

    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} 不能为空。")
    return normalized


app = FastAPI(title="Sandbox Runtime API")
app.include_router(router)
