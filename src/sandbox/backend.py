"""DeepAgents 沙箱后端协议的 AgentScope Runtime 实现。"""

from __future__ import annotations

import base64
import os
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

DEFAULT_CHROME_DEVTOOLS_PACKAGE = "chrome-devtools-mcp@0.23.0"
CHROME_DEVTOOLS_PACKAGE_ENV = "CHROME_DEVTOOLS_MCP_PACKAGE"
WORKSPACE_BIN_DIR = "/workspace/.local/bin"
NPM_TOOLS_PREFIX_DIR = "/workspace/.npm-tools"
NPM_CACHE_DIR = "/workspace/.npm-cache"


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

        command = self._with_runtime_environment(command)
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

    def inject_chrome_devtools(
        self,
        *,
        package: str | None = None,
        force: bool = False,
        timeout: int = 600,
    ) -> ExecuteResponse:
        """在当前沙箱工作区安装 chrome-devtools CLI。"""

        package = package or os.environ.get(
            CHROME_DEVTOOLS_PACKAGE_ENV,
            DEFAULT_CHROME_DEVTOOLS_PACKAGE,
        )
        return self.inject_npm_cli(
            package=package,
            commands=["chrome-devtools", "chrome-devtools-mcp"],
            force=force,
            install_name="chrome-devtools",
            timeout=timeout,
        )

    def inject_npm_cli(
        self,
        *,
        package: str,
        commands: list[str],
        force: bool = False,
        install_name: str | None = None,
        timeout: int = 600,
    ) -> ExecuteResponse:
        """在当前沙箱工作区安装 npm CLI 包，并暴露指定命令。

        `package` 是 npm 包规格，例如 `chrome-devtools-mcp@0.23.0`。
        `commands` 是要放进 `/workspace/.local/bin` 的命令入口。
        `install_name` 用来决定 npm prefix 目录名；不传时从第一个命令推导。
        """

        if not commands:
            raise ValueError("commands 不能为空。")

        command = self._npm_cli_install_command(
            package_spec=package,
            commands=commands,
            force=force,
            install_name=install_name or commands[0],
        )
        return self.execute(command, timeout=timeout)

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

    def _with_runtime_environment(self, command: str) -> str:
        """让沙箱内的动态注入命令在后续 execute 中可见。"""

        return (
            f"export PATH={shlex.quote(WORKSPACE_BIN_DIR)}:$PATH; "
            f"export npm_config_cache={shlex.quote(NPM_CACHE_DIR)}; "
            f"{command}"
        )

    def _npm_cli_install_command(
        self,
        *,
        package_spec: str,
        commands: list[str],
        force: bool,
        install_name: str,
    ) -> str:
        force_value = "1" if force else "0"
        safe_install_name = self._safe_npm_install_name(install_name)
        command_lines = "\n".join(shlex.quote(command) for command in commands)
        script = f"""set -eu
PACKAGE={shlex.quote(package_spec)}
FORCE={force_value}
PREFIX={shlex.quote(posixpath.join(NPM_TOOLS_PREFIX_DIR, safe_install_name))}
BIN_DIR={shlex.quote(WORKSPACE_BIN_DIR)}
NPM_CACHE={shlex.quote(NPM_CACHE_DIR)}
mkdir -p "$PREFIX" "$BIN_DIR" "$NPM_CACHE" /workspace/.cache
COMMANDS=$(cat <<'COMMANDS'
{command_lines}
COMMANDS
)

if [ "$FORCE" != "1" ]; then
  ALL_AVAILABLE=1
  for CMD in $COMMANDS; do
    if [ ! -x "$BIN_DIR/$CMD" ]; then
      ALL_AVAILABLE=0
      break
    fi
  done
  if [ "$ALL_AVAILABLE" = "1" ]; then
    echo "npm CLI already available: $COMMANDS"
    exit 0
  fi
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is required to inject npm CLI tools, but node was not found in the sandbox." >&2
  exit 127
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to inject npm CLI tools, but npm was not found in the sandbox." >&2
  exit 127
fi

node - <<'NODE'
const [major, minor] = process.versions.node.split('.').map(Number);
const supported =
  major > 22 ||
  (major === 22 && minor >= 12) ||
  (major === 20 && minor >= 19);
if (!supported) {{
  console.error(
    `npm CLI injection requires Node.js ^20.19.0 || ^22.12.0 || >=23; found ${{process.version}}.`,
  );
  process.exit(1);
}}
NODE

npm install --prefix "$PREFIX" --omit=dev --no-audit --no-fund "$PACKAGE"

for CMD in $COMMANDS; do
  if [ ! -x "$PREFIX/node_modules/.bin/$CMD" ]; then
    echo "Installed package does not expose command: $CMD" >&2
    exit 1
  fi
  cat > "$BIN_DIR/$CMD" <<EOF
#!/bin/sh
export HOME=/workspace
export XDG_CACHE_HOME=/workspace/.cache
export npm_config_cache=/workspace/.npm-cache
exec "$PREFIX/node_modules/.bin/$CMD" "\\$@"
EOF
  chmod +x "$BIN_DIR/$CMD"
done

echo "npm CLI injected: $COMMANDS"
"""
        return f"sh -lc {shlex.quote(script)}"

    def _safe_npm_install_name(self, value: str) -> str:
        safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in value)
        return safe.strip(".-_") or "tool"
