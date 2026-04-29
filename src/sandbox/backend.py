"""DeepAgents 沙箱后端协议的 AgentScope Runtime 实现。"""

from __future__ import annotations

import base64
import posixpath
import shlex
from typing import Any

from agentscope_runtime.sandbox import BaseSandbox as AgentScopeBaseSandbox
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox as DeepAgentsBaseSandbox


class AgentScopeDeepAgentsBackend(DeepAgentsBaseSandbox):
    """把 AgentScope Runtime 沙箱包装成 DeepAgents 沙箱后端。

    DeepAgents 的 `BaseSandbox` 已经基于 `execute()` 实现了 `ls/read/write/edit/glob/grep`。
    因此这里仅实现协议要求的 `id`、`execute`、`upload_files`、`download_files`。
    """

    def __init__(
        self,
        sandbox: AgentScopeBaseSandbox,
        *,
        release_on_close: bool = False,
    ) -> None:
        self.sandbox = sandbox
        self.release_on_close = release_on_close

    @classmethod
    def from_existing(
        cls,
        sandbox: AgentScopeBaseSandbox,
        *,
        release_on_close: bool = False,
    ) -> "AgentScopeDeepAgentsBackend":
        return cls(sandbox=sandbox, release_on_close=release_on_close)

    @property
    def id(self) -> str:
        """返回 DeepAgents 用来标识当前沙箱的 id。"""

        sandbox_id = self.sandbox.sandbox_id
        if not sandbox_id:
            raise RuntimeError("AgentScope sandbox 尚未启动。")
        return sandbox_id

    def close(self) -> None:
        """在需要时释放底层 AgentScope 沙箱。"""

        if self.release_on_close:
            self.sandbox.close()

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """执行 DeepAgents 传入的 shell 命令，并转换成 DeepAgents 的响应格式。"""

        if timeout is not None and timeout > 0:
            # DeepAgents 的 execute 支持 timeout，这里转成沙箱内的 shell timeout。
            command = f"timeout {int(timeout)}s sh -lc {shlex.quote(command)}"

        raw = self.sandbox.run_shell_command(command)
        output, exit_code = self._parse_tool_response(raw)
        return ExecuteResponse(
            output=output,
            exit_code=exit_code,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """把 DeepAgents 文件写入 AgentScope 沙箱的 /workspace。"""

        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                safe_path = self._sandbox_path(path)
                parent = posixpath.dirname(safe_path)
                # 文件内容通过 base64 放进 shell 命令，避免换行和特殊字符破坏命令结构。
                encoded = base64.b64encode(content).decode("ascii")
                command = (
                    f"mkdir -p {shlex.quote(parent)} && "
                    f"python -c {shlex.quote(self._decode_script(encoded, safe_path))}"
                )
                result = self.execute(command)
                if result.exit_code == 0:
                    responses.append(FileUploadResponse(path=path, error=None))
                else:
                    responses.append(FileUploadResponse(path=path, error=result.output))
            except Exception as exc:  # noqa: BLE001
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从 AgentScope 沙箱读取文件并返回给 DeepAgents。"""

        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                safe_path = self._sandbox_path(path)
                # AgentScope 的文件 API 不直接暴露给 DeepAgents，这里通过沙箱内 Python 读文件再 base64 返回。
                command = (
                    "python -c "
                    + shlex.quote(
                        "import base64, pathlib; "
                        f"p=pathlib.Path({safe_path!r}); "
                        "print(base64.b64encode(p.read_bytes()).decode('ascii'))",
                    )
                )
                result = self.execute(command)
                if result.exit_code == 0:
                    content = base64.b64decode(result.output.strip())
                    responses.append(
                        FileDownloadResponse(path=path, content=content, error=None),
                    )
                else:
                    responses.append(
                        FileDownloadResponse(path=path, content=None, error=result.output),
                    )
            except Exception as exc:  # noqa: BLE001
                responses.append(
                    FileDownloadResponse(path=path, content=None, error=str(exc)),
                )
        return responses

    def _sandbox_path(self, path: str) -> str:
        """把 DeepAgents 虚拟文件路径映射到 AgentScope 挂载的 /workspace。"""

        if not path:
            return "/workspace"

        normalized = posixpath.normpath("/" + path.lstrip("/"))
        if normalized == ".." or normalized.startswith("../"):
            raise ValueError(f"不允许访问工作区外路径：{path}")
        if normalized == "/workspace" or normalized.startswith("/workspace/"):
            return normalized
        return posixpath.join("/workspace", normalized.lstrip("/"))

    def _parse_tool_response(self, raw: Any) -> tuple[str, int | None]:
        """兼容 AgentScope Runtime 不同版本返回的 shell 执行结果格式。"""

        if isinstance(raw, str):
            return raw, None

        if isinstance(raw, list):
            output_parts: list[str] = []
            exit_code: int | None = None
            for item in raw:
                text = getattr(item, "text", None)
                description = getattr(item, "description", None)
                if isinstance(item, dict):
                    text = item.get("text", text)
                    description = item.get("description", description)
                if text is None:
                    continue
                if description == "returncode":
                    try:
                        exit_code = int(str(text).strip())
                    except ValueError:
                        exit_code = None
                else:
                    output_parts.append(str(text))
            return "".join(output_parts), exit_code

        if isinstance(raw, dict):
            content = raw.get("content")
            if isinstance(content, list):
                return self._parse_tool_response(content)
            output = raw.get("stdout") or raw.get("output") or raw.get("result") or ""
            exit_code = raw.get("returncode") or raw.get("exit_code")
            return str(output), int(exit_code) if exit_code is not None else None

        return str(raw), None

    def _decode_script(self, encoded: str, path: str) -> str:
        return (
            "import base64, pathlib; "
            f"pathlib.Path({path!r}).write_bytes(base64.b64decode({encoded!r}))"
        )
