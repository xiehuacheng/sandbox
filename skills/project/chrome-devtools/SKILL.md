---
name: chrome-devtools
description: 在当前 BrowserSandbox 中调试浏览器、打开网页、读取页面 snapshot、截图、查看 console 日志和 network 网络请求时使用；通过 execute 调用 chrome-devtools CLI。
allowed-tools: execute read_file write_file
---

# Chrome DevTools CLI

当任务需要浏览器自动化、网页检查、截图、读取页面结构、查看 console 日志、查看 network 请求或读取 DOM/page snapshot 时，使用这个 skill。

命令是 `chrome-devtools`。它运行在当前 AgentScope BrowserSandbox 内，不控制宿主机浏览器。

## 工作流

直接用 `execute` 运行 Chrome DevTools 命令。后台 daemon 会自动启动，不要在每次命令前运行 `chrome-devtools start`、`status` 或 `stop`。

打开页面：

```bash
chrome-devtools new_page "https://example.com"
```

读取当前页面 snapshot：

```bash
chrome-devtools take_snapshot
```

读取页面标题：

```bash
chrome-devtools evaluate_script "() => document.title"
```

查看 console 消息：

```bash
chrome-devtools list_console_messages
```

查看 network 请求：

```bash
chrome-devtools list_network_requests
```

保存截图到 `/workspace`：

```bash
chrome-devtools take_screenshot --filePath /workspace/page.png
```

## 交互规则

- 点击、输入或读取元素文字前，先用 `take_snapshot` 获取最新页面状态。
- `click`、`fill`、`hover` 等元素命令必须使用最新 snapshot 里的 UID。
- 如果命令返回 timeout 或 locator error，重新 `take_snapshot`，根据当前页面状态继续判断，不要直接失败。
- 需要结构化输出时使用 `--output-format=json`。

## 示例

```bash
chrome-devtools new_page "https://example.com"
chrome-devtools take_snapshot
```

```bash
chrome-devtools evaluate_script "() => ({ title: document.title, url: location.href })" --output-format=json
```
