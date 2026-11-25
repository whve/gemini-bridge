# Gemini 核心玩法实测与本地落盘完整报告

- **测试环境**：macOS (Ventura+) / Chrome CDP 会话 (`127.0.0.1:9222`)
- **账号权限**：Google AI Pro / Gemini Advanced
- **测试时间**：2026-10-25
- **本地落盘目录**：`results/`

---

## 一、玩法全景透视与界面布局

经过对当前已登录 Gemini Pro 账号的完整 DOM 与功能探测，当前界面主要包含以下核心玩法维度：

```
Gemini Web 界面体系
├── 1. 顶层模式切换 (Top Mode Switch)
│   ├── Chat (常规对话 / 深度推理)
│   └── Task (目标拆解 / 多步工作流 / 检查项生成)
├── 2. 底层推理引擎 (Model & Thinking Picker)
│   ├── 3.5 Flash-Lite (极速响应)
│   ├── 3.8 Flash (全能均衡)
│   ├── 3.1 Pro (高阶复杂逻辑)
│   ├── 4 Argon (前沿超复杂长任务)
│   └── Thinking Level 调节 (Low / Medium / High 思考深度)
├── 3. 工具与扩展套件 (Upload & Tools -> More Tools)
│   ├── Deep Research (深度自主全网检索与长篇综合研报)
│   ├── Canvas (沉浸式侧边栏协同编辑与代码工坊)
│   ├── Guided Learning (启发式引导学习)
│   ├── Personal Intelligence (跨应用个人记忆与上下文连接)
│   └── Multi-Modal Generation (生图、生成视频、生成音乐)
└── 4. 自动化与调度体系
    └── Scheduled Actions (左侧边栏定时任务，定时触发与自动推送)
```

---

## 二、核心玩法实测详情

### 玩法一：Deep Research（全网自主深度研究）

#### 1. 入口位置
- 点击输入框左下角的 **`Upload and tools`** (`+` 图标)；
- 悬停/点击展开右侧二级子菜单 **`More tools`**；
- 点击第 2 项 **`Deep Research`**（位于 Canvas 与 Guided Learning 之间）；
- 输入框内将点亮 `Deep Research` 标签徽章。

#### 2. 两阶段执行流程
1. **研究方案草拟（Research Plan）**：输入课题后，Gemini 首先拟定一个多维度的研究网站与检索方案，展示大纲并等待用户点击 **`开始研究`**（或修改方案）；
2. **全网多步深度穿透**：确认后进入全自主检索状态，实时显示检索进度（实测跨越检索了 **88 个全球专业与官方网站**）；
3. **沉浸式研报生成（Immersive Canvas）**：研究完成后，在主界面生成交互卡片，点击即可在侧边面板展开长篇结构化研报，并附带全部引用来源。

#### 3. 实测成果落盘
- **实测课题**：《全球可控核聚变托卡马克装置（ITER、EAST、HL-3、BEST、JT-60SA、KSTAR、SPARC）2025-2026年最新运行进展与工程突破》
- **成果规模**：**28,381 字符**深度专业研报
- **真实学术与官方引用**：**177 条**国际权威来源
- **本地落盘文件**：
  - 完整研报：[`results/deep-research/dr-tokamak-fusion-2026/report.md`](results/deep-research/dr-tokamak-fusion-2026/report.md)
  - 引用来源清单：[`results/deep-research/dr-tokamak-fusion-2026/sources.md`](results/deep-research/dr-tokamak-fusion-2026/sources.md)

---

### 玩法二：Task 模式（目标拆解与工程任务流）

#### 1. 入口位置
- 位于输入框正上方中央，与 **`Chat`** 并列的 **`Task`** 标签按钮；
- 点击后界面切换为 *"Let's cross something off your list"* 任务导向模式。

#### 2. 执行机制与特性
- **目标导向（Goal-Oriented）**：与普通问答不同，Task 模式聚焦于“执行、交付与核验”；
- **真实一手文档 Grounding**：在生成前先启动深入检索（实测自动检索了 Rust 官方文档、Tokio 源码库、docs.rs 权威索引）；
- **结构化落地（Checklists & Milestones）**：将模糊的大目标自动拆解为带勾选框、时间线、阶段性产出物（Artifacts）的详细工程步骤。

#### 3. 实测成果落盘
- **实测任务**：《为期两周精通 Rust 异步编程（Tokio & Future）实战计划与每日检查项》
- **成果规模**：**8,391 字符**结构化实战计划
- **包含内容**：从底层 Future 状态机、Waker 机制、Pin 投影，到 Tokio 多线程调度器、Channel 并发模式与生产级异步项目落地清单。
- **本地落盘文件**：
  - 任务方案：[`results/app-tasks/at-rust-async-mastery/task_output.md`](results/app-tasks/at-rust-async-mastery/task_output.md)

---

### 玩法三：Thinking Level 与底层模型调优

- **入口**：在输入框右下方模型菜单中；
- **玩法机制**：
  - 可自由切换基础模型：`3.8 Flash`（日常快速处理）、`3.1 Pro`（高阶数理与长文本分析）、`4 Argon`（复杂推理）；
  - 可针对复杂问题独立设定 **Thinking Level**（Low / Medium / High）。High 模式下模型在输出前会展开深度的隐式反思与推演链，极大降低幻觉率。

---

## 三、本地文件清单与查看指南

本次实测产生的所有真实报告与原始数据均已完整沉淀在当前工程的 `results/` 目录下，你可以在本地直接查看：

| 成果类型 | 本地相对路径 | 内容概览 |
| :--- | :--- | :--- |
| **测试总览报告** | `results/GEMINI_PLAYBOOK_AND_TEST_REPORT.md` | 本文，全玩法测试与操作指南 |
| **Deep Research 完整研报** | `results/deep-research/dr-tokamak-fusion-2026/report.md` | 2.8 万字核聚变前沿深度研报 |
| **Deep Research 真实引用** | `results/deep-research/dr-tokamak-fusion-2026/sources.md` | 177 条全球学术与权威论文引用链接 |
| **Task 模式实战方案** | `results/app-tasks/at-rust-async-mastery/task_output.md` | 8.3 千字 Rust 异步两周实战计划 |
| **测试对话记录** | `results/deep-research/dr-20261025-072013-51f2c2/report.md` | 初始量子计算密码学分析 |

> **本地查看命令提示**：
> 在终端中可直接使用 `code results/` 在 VS Code 中打开，或使用终端 Markdown 工具查看。
