"""DeepAgents + AgentScope Runtime BrowserSandbox 集成示例。"""

from __future__ import annotations

import os

# 必须在导入 agentscope_runtime.sandbox 之前设置，让服务器默认使用 Docker 后端。
os.environ.setdefault("CONTAINER_DEPLOYMENT", "docker")

from .backend import AgentScopeDeepAgentsBackend

__all__ = ["AgentScopeDeepAgentsBackend"]
