# Gemini Bridge (`gemini-bridge`)

> **MIT License** · **Python 3.11+** · **macOS & Linux** · **Crafted by ve & Her**

### 项目定位

`gemini-bridge` 是面向本地 AI Agent（Claude Code、Codex、Cursor 等）的任务桥接中间件，打通本地编码工作流与云端 **Gemini Deep Research** / **Gemini App Tasks**：

- **双后端无缝切换**：优先通过 Playwright 复用本地持久化 Chrome 会话（零侵入复用网页端订阅配额），并支持官方 Gemini Interactions API 作为无浏览器备选后端。
- **异步后台状态机**：基于 SQLite（WAL 模式）管理长耗时任务队列、防重保护与孤儿进程安全恢复。
- **原生 CLI + 零依赖 MCP Server**：内置标准 JSON-RPC 2.0 STDIO MCP 服务与 `--json` 结构化输出，开箱即插主流 AI 编程助手。
- **本地产物自动同步**：任务完成后自动落盘完整的 `report.md`、`sources.md`、`metadata.json` 与脱敏 `task.log`。

### ⚡ 极速 30 秒上手指南（Quickstart）

1. **安装环境并初始化配置**

```bash
git clone https://github.com/whve/gemini-bridge.git && cd gemini-bridge
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[browser]" && playwright install chromium
cp config.example.yaml config.yaml
```

2. **启动独立 Chrome 会话登录 Gemini 并自检**

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.gemini-bridge/chrome-profile" \
  "https://gemini.google.com/app"
# 在弹出窗口完成 Google 登录后，验证就绪状态：
gemini-bridge doctor
```

3. **提交首个深度研究任务**

```bash
gemini-bridge submit --mode deep-research --prompt "调研 2026 年主流 AI Coding Agent 架构演进并附带引用来源"
```

---

## 一、官方功能核实结论（核实日期：2026-10-25）

在开发本项目前，已严格核实 Google 最新官方文档，明确区分以下四项不同机制：

| 功能 / 接口名称 | 官方定位与实际入口 | 鉴权与订阅要求 | 公开 API 支持情况 | 浏览器自动化可行性与边界 | 官方来源链接 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Gemini 网页版 Deep Research** | `https://gemini.google.com/app` 模型菜单或输入框工具栏中的 **Deep Research** 模式；采取“生成研究计划 → 点击 **Start research** 确认 → 多步搜索与阅读 → Canvas 渲染报告”的两阶段流程。 | 个人 Google 账号（免费层有次数配额限制；Google AI Pro / Ultra 享有更高配额）或受支持的 Google Workspace 账号。 | **无公开接口可直接复用网页端订阅配额**。Google 未开放任何允许第三方通过 Cookie/Session 直接调用的公开 Web API。 | **可行（第一优先级）**：通过 Playwright 连接用户已登录的 Chrome CDP 会话或独立持久化 Profile，可自动填入 Prompt、点击 `Start research` 确认研究计划、监测进度并在完成后提取 Markdown 报告及全部真实引用链接。 | [Gemini Deep Research Updates](https://workspaceupdates.googleblog.com/2025/05/deep-research-updates-gemini-io-2025.html) / [Deep Research Workspace Integration](https://workspaceupdates.googleblog.com/2025/11/gemini-deep-research-integrates-workspace-content.html) |
| **Gemini API Deep Research Agent** | 仅通过 **Interactions API** (`POST /v1beta/interactions`) 提供，Agent 标识符为 `deep-research-preview-04-2026`（标准版）与 `deep-research-max-preview-04-2026`（深度版）。**不支持**通过普通 `generate_content` 调用。 | 必须在 Google AI Studio 配置 `GEMINI_API_KEY` 并启用 **按量付费 (Pay-as-you-go)**。**网页端 Google AI Pro/Ultra 订阅不包含 API 额度**（单次研究预估成本约 $1.00–$3.00，Max 版约 $3.00–$7.00）。 | **官方原生支持（第二优先级可选后端）**：必须设置 `background=true` 异步执行，返回 `interaction.id` 供轮询状态（`in_progress` → `completed` / `failed`），最长运行 60 分钟。API 版无法直接导出到个人 Google Docs，但支持自定义 MCP Server 与文件搜索。 | 不适用（纯 REST / SDK 接口）。 | [Gemini API Deep Research Docs](https://ai.google.dev/gemini-api/docs/deep-research) / [Deep Research Max Blog](https://blog.google/innovation-and-ai/models-and-research/gemini-models/next-generation-gemini-deep-research/) |
| **Gemini App Tasks (Tasks in Gemini / Spark) & Scheduled Actions** | 包含两类网页/移动端功能：<br>1. **Tasks in Gemini (Spark)**：在 `gemini.google.com` 输入框上方点击 **Task**，管理跨应用多步任务并支持任务级定时。<br>2. **Scheduled Actions（定时操作）**：在聊天中要求定时执行，并在 `Settings & help` → `Scheduled actions` 页面管理（上限 10 个活动计划）。 | **Tasks in Gemini** 要求：年满 18 岁、**仅限个人 Google 账号**（Workspace 企业/教育账号暂不支持）、订阅 **Google AI Pro 或 Ultra**、开启 Keep Activity 与 Personal Intelligence。<br>**Scheduled Actions** 要求：AI Pro/Ultra 或符合条件的 Workspace 订阅。 | **无任何公开 API**。Google 未开放任何 REST API 或 SDK 用于创建、查询、暂停或读取网页端 Gemini App Tasks 或 Scheduled Actions。 | **受限可行**：仅当所连接的 Chrome 会话满足个人 Google AI Pro/Ultra 订阅且界面开放 `Task` 入口时，可通过 Playwright 操作；若任务执行中触发浏览器接管请求（`Take over`），必须由人工介入。若账号无权限或入口不可见，`gemini-bridge` 会明确标记为 `needs_user_action` 并给出具体原因，绝不伪造成功。 | [Manage your tasks & workflows in Gemini Apps](https://support.google.com/gemini/answer/17094507) / [Schedule actions in Gemini Apps](https://support.google.com/gemini/answer/16316416) |
| **Gemini API Antigravity Agent** | 开发者级托管沙箱 Agent（`antigravity-preview-09-2026`），通过 Interactions API 调用，在云端 Linux 沙箱中执行代码、文件读写与网页检索，并支持 API 级 Cron Triggers。 | Google AI Studio `GEMINI_API_KEY` 按量计费（与网页端 Gemini App Tasks 完全独立）。 | **官方支持**（Interactions API）。在 `gemini-bridge` 中严格与网页端 `app-task` 区分，仅当用户显式开启 `allow_api_fallback_for_app_task: true` 时才作为可选替代后端。 | 不适用（纯 API 接口）。 | [Antigravity Agent Gemini API Docs](https://ai.google.dev/gemini-api/docs/antigravity-agent) |

---

## 二、目录结构

```text
gemini-bridge/
├── README.md
├── LICENSE
├── pyproject.toml
├── config.example.yaml
├── .gitignore
├── src/
│   └── gemini_bridge/
│       ├── __init__.py
│       ├── cli.py                # 命令行接口 (submit/status/wait/fetch/list/resume/doctor/mcp/serve)
│       ├── config.py             # 配置加载与敏感凭据脱敏 (API Key / Cookie / Token 自动过滤)
│       ├── browser.py            # Gemini 网页 DOM 适配层、选择器注册表、JS 报告与引用提取器
│       ├── tasks.py              # SQLite (WAL 模式) 任务状态机与防重复提交保护
│       ├── storage.py            # 本地文件自动同步 (report.md / metadata.json / sources.md / task.log)
│       ├── worker.py             # 独立后台进程管理、孤儿进程检测与安全恢复逻辑
│       ├── mcp_server.py         # 标准 JSON-RPC 2.0 STDIO MCP Server (供 Claude Code / Codex 直接调用)
│       ├── http_server.py        # 可选本地 HTTP 接口 (强制仅监听 127.0.0.1)
│       └── backends/
│           ├── __init__.py
│           ├── base.py           # 统一后端抽象接口
│           ├── web_backend.py    # 第一优先级：Playwright 浏览器会话复用后端 (CDP / 持久化 Profile)
│           ├── api_backend.py    # 第二优先级：官方 Gemini Interactions API 后端
│           └── mock_backend.py   # 本地确定性测试后端 (仅用于离线单元测试与 CI 验证)
├── prompts/
│   ├── research.md               # Deep Research 示例 Prompt
│   └── task.md                   # Gemini App Task 示例 Prompt
├── data/
│   ├── tasks.db                  # SQLite 持久化任务数据库
│   └── logs/                     # 任务与系统运行日志 (已脱敏)
├── results/
│   ├── deep-research/            # Deep Research 结果目录 (<task_id>/report.md, metadata.json, sources.md, task.log)
│   └── app-tasks/                # App Tasks 结果目录
└── tests/
    └── test_gemini_bridge.py     # 单元测试与端到端集成测试套件
```

---

## 三、MacBook 安装与配置指南

### 1. 创建虚拟环境并安装

在 MacBook 终端（兼容 macOS Ventura 及更新版本，Python 3.11+）中执行：

```bash
cd gemini-bridge
python3 -m venv .venv
source .venv/bin/activate

# 安装核心包及 Playwright 浏览器支持
pip install -e ".[browser,dev]"
playwright install chromium

# 生成本地配置文件
cp config.example.yaml config.yaml
```

### 2. 连接或登录现有 Gemini 网页账号（第一优先级，推荐）

为了安全复用你现有的 Gemini 网页订阅，同时**不读取、不复制、不修改你日常使用的默认 Chrome 配置文件或 Cookie 数据库**，推荐使用 **独立持久化 Profile + CDP 远程调试端口**：

```bash
# 在 MacBook 终端启动带有独立 Profile 的 Google Chrome：
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.gemini-bridge/chrome-profile" \
  "https://gemini.google.com/app"
```

1. **首次运行**：在弹出的 Chrome 窗口中手动登录你的 Google 账号（登录状态会持久化保存在 `~/.gemini-bridge/chrome-profile` 中，后续无需重复登录）。
2. **验证就绪状态**：
   ```bash
   gemini-bridge doctor
   ```
   当看到 `Web Browser Backend -> Status: READY` 时，即可随时通过 CLI 或 MCP 提交任务。

### 3. 可选：配置官方 Gemini API 后端（第二优先级）

如果你希望在无浏览器环境下调用官方 Interactions API（注意：使用 AI Studio 按量计费，不复用网页端会员额度）：

```bash
export GEMINI_API_KEY="AIzaSyYourApiKeyHere"
gemini-bridge submit --mode deep-research --backend api --file prompts/research.md
```

---

## 四、完整 CLI 命令用法

所有命令均支持 `--json` 参数，方便 Claude Code、Codex 或 Shell 脚本直接解析结构化 JSON：

```bash
# 1. 查看帮助
gemini-bridge --help

# 2. 检查环境、SQLite 数据库与浏览器登录连接状态
gemini-bridge doctor
gemini-bridge doctor --json

# 3. 从文件提交 Deep Research 任务（后台异步执行，立即返回 task_id）
gemini-bridge submit \
  --mode deep-research \
  --file prompts/research.md

# 4. 直接提交文本 Prompt（支持附加 --wait 同步等待完成，或 --json 返回 JSON）
gemini-bridge submit \
  --mode deep-research \
  --prompt "研究固态电池 2025-2026 量产进展，并提供可靠来源链接" \
  --json

# 5. 尝试提交 Gemini App Task
gemini-bridge submit \
  --mode app-task \
  --file prompts/task.md

# 6. 查询任务状态（关闭终端后重新打开仍可查询）
gemini-bridge status <task_id>
gemini-bridge status <task_id> --json

# 7. 阻塞等待后台任务结束（支持设置超时秒数 --timeout）
gemini-bridge wait <task_id> --timeout 1800

# 8. 获取或重新同步任务结果到本地文件（不重复提交任务）
gemini-bridge fetch <task_id>
gemini-bridge fetch <task_id> --print-report

# 9. 查看历史任务列表（支持按 --mode 或 --status 过滤）
gemini-bridge list
gemini-bridge list --mode deep-research --status completed --json

# 10. 安全恢复中断或处于 needs_user_action 状态的任务
gemini-bridge resume <task_id>
```

---

## 五、在其他 AI Agent (Claude Code / Codex / Cursor) 中通过 MCP 调用

`gemini-bridge` 内置了零外部依赖的标准 **JSON-RPC 2.0 STDIO MCP Server**，暴露以下 5 个结构化工具：

- `submit_task`：提交 `deep-research` 或 `app-task` 任务
- `get_task_status`：查询任务状态与本地文件路径
- `wait_for_task`：等待任务完成
- `get_task_result`：读取完整 Markdown 报告与验证过的引用来源列表
- `list_tasks`：列出历史任务

### 在 Claude Code 中注册 MCP Server

```bash
claude mcp add gemini-bridge -- /path/to/gemini-bridge/.venv/bin/gemini-bridge mcp
```

或在 `.mcp.json` / `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "gemini-bridge": {
      "command": "/path/to/gemini-bridge/.venv/bin/gemini-bridge",
      "args": ["--home", "/path/to/gemini-bridge", "mcp"]
    }
  }
}
```

---

## 六、每个任务自动同步的本地文件清单

每个任务在 `results/deep-research/<task_id>/`（或 `results/app-tasks/<task_id>/`）下自动生成并同步 4 个 UTF-8 文件：

1. `report.md`：完整研究报告或任务输出，保留原始章节层级与行内引用链接。
2. `metadata.json`：任务 ID、类型、后端、状态、DOM 检测状态、时间戳、文件路径及错误提示。
3. `sources.md`：从报告正文与网页引用卡片中提取并去重的真实来源 URL 与标题（严禁伪造未验证来源）。
4. `task.log`：完整记录任务创建、后台进程启动、状态轮询、异常提示与恢复操作的脱敏日志。
