---
name: runpython
description: 在当前 AgentScope 沙箱中运行 Python 代码、计算、数据处理或读取文件时使用；通过 execute 调用 runpython 命令。
allowed-tools: execute read_file write_file
---

# 在沙箱中运行 Python

当任务需要在当前 AgentScope 沙箱中执行 Python、做快速计算、数据处理、读取文件或生成文件时，使用这个 skill。

## 命令

始终通过 `execute` 调用沙箱命令 `runpython` 来运行 Python。

内联代码使用这个模式：

```bash
runpython <<'PY'
print(1 + 1)
PY
```

读取或写入文件时，使用 `/workspace/...` 路径：

```bash
runpython <<'PY'
from pathlib import Path
path = Path("/workspace/input.txt")
print(path.read_text())
PY
```

## 规则

- 不要在 `execute` 中直接调用 `python`、`python3`、`ipython` 或 `python -c`。
- 不要为了一个小计算先写临时 `.py` 文件再执行。
- Python 片段、一次性数据处理、文件解析都使用 `runpython`。
- 如果用户明确要求测试一个命令行 Python 脚本，可以使用 `runpython /workspace/script.py`。
- DeepAgents 文件工具里的 `/foo.txt` 对应沙箱内 `/workspace/foo.txt`。

## 示例

```bash
runpython <<'PY'
import json
data = {"answer": 42}
print(json.dumps(data, ensure_ascii=False))
PY
```

```bash
runpython --code 'print("hello from sandbox")'
```
