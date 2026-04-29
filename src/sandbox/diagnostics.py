"""LangGraph/DeepAgents 运行时诊断日志。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import sys
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langgraph.runtime import Runtime


def log(message: str) -> None:
    """把诊断信息打印到 LangGraph dev 控制台。"""

    print(f"[sandbox] {message}", file=sys.stderr, flush=True)


def _message_count(state: Any) -> int:
    """从 agent state 中读取消息数量，读取失败时返回 0。"""

    if isinstance(state, dict):
        messages = state.get("messages")
        if isinstance(messages, list):
            return len(messages)
    return 0


def _tool_names(tools: list[Any]) -> str:
    """把模型请求里的工具名压缩成一行日志。"""

    names: list[str] = []
    for tool in tools:
        if isinstance(tool, dict):
            name = tool.get("name")
        else:
            name = getattr(tool, "name", None)
        if isinstance(name, str) and name:
            names.append(name)
    return ", ".join(names) if names else "<none>"


class DiagnosticsMiddleware(AgentMiddleware):
    """记录一次 LangGraph run 是否真正进入 agent 和模型节点。"""

    async def abefore_agent(
        self,
        state: Any,
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """agent run 开始时打印消息数量。"""

        log(f"进入 agent run：messages={_message_count(state)}")
        return None

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """模型调用前后打印工具列表，用来判断沙箱工具是否进入模型请求。"""

        log(f"准备调用模型：messages={len(request.messages)} tools={_tool_names(request.tools)}")
        response = await handler(request)
        log("模型调用完成")
        return response
