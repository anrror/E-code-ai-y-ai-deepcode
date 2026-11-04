# DeepCode 中文说明（代码架构分析 + DeepSeek 接入指南）

> 本文是**第三方中文解读**，不是官方翻译。官方中文版见 [README_ZH.md](README_ZH.md)，英文版见 [README.md](README.md)。
> 官方版本侧重产品与安装，本文补充两块官方文档没有展开的内容：**DeepSeek 到底支持到什么程度**，以及**这套代码是怎么组织的**。

| 项目 | 值 |
|---|---|
| 仓库 | [HKUDS/DeepCode](https://github.com/HKUDS/DeepCode)（香港大学 Data Intelligence Lab） |
| 版本 | `2.3.1`（`core/version.py`） |
| 分析基线 | commit `aa6ed16`，2026-11-02 |
| 许可证 | MIT |
| 语言/运行时 | Python ≥ 3.12；桌面端 Rust + TypeScript |
| PyPI 包名 | `deepcode-hku` |
| 定位 | 开源 Coding Agent：TUI / Desktop / Web / Headless 四端共用一套本地服务 |

---

## 目录

1. [项目定位](#1-项目定位)
2. [DeepSeek 支持情况](#2-deepseek-支持情况)
3. [快速开始](#3-快速开始)
4. [代码架构分析](#4-代码架构分析)
5. [目录速查](#5-目录速查)
6. [设计取舍评价](#6-设计取舍评价)
7. [常用命令速查](#7-常用命令速查)
8. [文档索引](#8-文档索引)

---

## 1. 项目定位

### 1.1 从 Paper2Code 到 Agent Harness

DeepCode 的前身是 Paper2Code —— 一个把论文/技术文档转成可运行代码的多智能体系统，在 PaperBench 人类专家子集上拿到 **75.9%**，高于当时最好的人类基线 **72.4%**（论文 [arXiv:2512.07921](https://arxiv.org/abs/2512.07921)）。

v2.0 之后项目重心转移：Paper2Code 降级为其中一个功能（`workflows/` + Desktop/Web 的 `/paper`），主线变成通用的 **Coding Agent**。仓库现在的形态更接近「自研 Agent Harness + 本地守护服务 + 多客户端」，而不是一个论文复现脚本。

### 1.2 四端一套状态

```
                  ┌──────────────────────────────┐
  deepcode        │  TUI (prompt_toolkit)        │
  deepcode desktop│  Desktop (Tauri 2 + React)   │
  deepcode web    │  Web (同前端产物 + 浏览器)   │
  deepcode exec   │  Headless（脚本/CI）         │
                  └──────────────┬───────────────┘
                                 │ JSON-RPC（stdio / WebSocket）
                  ┌──────────────▼───────────────┐
                  │ app_server：127.0.0.1:3081    │
                  │ 校验输入、翻译错误、不分发 SQL│
                  └──────────────┬───────────────┘
                  ┌──────────────▼───────────────┐
                  │ core/application：用例编排    │
                  │ TurnService / ApprovalService │
                  └───────┬──────────────┬───────┘
                          │              │
        ┌─────────────────▼──┐   ┌───────▼──────────────┐
        │ core/persistence    │   │ core/agent_runtime    │
        │ SQLite（可重建投影）│   │ AgentRunner / 工具循环│
        └────────────────────┘   └───────┬──────────────┘
                                         │
                        ┌────────────────▼─────────────────┐
                        │ core/harness：权限 / 沙箱 / 审批  │
                        │ core/providers：模型适配          │
                        └──────────────────────────────────┘
```

关键点：**关闭客户端不会停掉正在跑的 Turn**。Turn 在本地守护服务里执行，TUI 关掉后可以在浏览器里接着审批。项目、会话、模型、Skills、权限、Goal、Automation 全部共享。

---

## 2. DeepSeek 支持情况

### 2.1 结论

**原生支持，而且是内置一等公民。** 不是「用 OpenAI 兼容端点凑合」，而是在 Provider 注册表里单独声明了 `deepseek`：

- 有专属模板名 `deepseek`（`deepcode provider set --template deepseek`）
- 有自己的默认 API 地址与默认环境变量
- 有自己的思考开关协议（`thinking: {"type": ...}`，默认开启）
- 模型目录里预置了 5 个 DeepSeek 模型 ID 及其上下文窗口、输出上限、单价
- 有模型族前缀规则，新出的型号不会掉到错误的默认行

### 2.2 代码证据

| 能力 | 文件与位置 | 内容 |
|---|---|---|
| Provider 声明 | `core/providers/registry.py:286-296` | `name="deepseek"`、`env_key="DEEPSEEK_API_KEY"`、`backend="openai_compat"`、`default_api_base="https://api.deepseek.com"`、`thinking_style="thinking_type"`、`thinking_default_on=True` |
| 配置模型 | `core/config.py:336` | `deepseek: ProviderConfig = Field(default_factory=ProviderConfig)` |
| 模型目录 | `core/providers/catalog.py:245-272` | `deepseek-v3` / `deepseek-r1` / `deepseek-flash` / `deepseek-v4-flash` / `deepseek-v4-pro` |
| 族前缀规则 | `core/providers/catalog.py:349-357` | `deepseek-r` → r1，`deepseek-flash` → flash，`deepseek-v4` → v4-flash，`deepseek` → v3 |
| 前缀识别 | `core/providers/catalog_service.py:556` | `"deepseek": ("deepseek-",)` |
| 用量解析 | `core/providers/openai_compat.py:817` | 识别 DeepSeek / SiliconFlow 的 `prompt_cache_hit_tokens` |
| 官方文档 | `README.md:435` | DeepSeek 列入 14 个内置模板 |

实测（用 CPython 3.14 直接导入模块，不依赖第三方包）：

```text
>>> [p.name for p in PROVIDERS]
['custom', 'openrouter', 'requesty', 'opper', 'forge', 'bedrock', 'anthropic',
 'openai', 'deepseek', 'gemini', 'zhipu', 'dashscope', 'minimax', 'vllm', 'ollama']
```

### 2.3 内置 DeepSeek 模型清单

数据来源 `core/providers/catalog.py`，价格单位：美元 / 百万 tokens（代码注释标注读取自 2026-09-27 的官方定价页）。

| 模型 ID | 上下文 | 最大输出 | 输入价 | 输出价 | 思考 |
|---|---:|---:|---:|---:|---|
| `deepseek-v3` | 128,000 | 8,192 | 0.27 | 1.10 | 可开关 |
| `deepseek-r1` | 128,000 | 65,536 | 0.55 | 2.19 | **强制开启** |
| `deepseek-flash`（V4.1-Flash 现名） | 1,000,000 | 384,000 | 0.30 | 1.20 | 可开关 |
| `deepseek-v4-flash`（保留别名，仍可计费） | 1,000,000 | 384,000 | 0.30 | 1.20 | 可开关 |
| `deepseek-v4-pro` | 1,000,000 | 384,000 | 1.32 | 3.96 | 可开关 |

### 2.4 三步接入

```console
# 1. 创建连接（走 DeepSeek 官方端点；协议保持 auto，
#    实际适配器由模板 backend=openai_compat 解析为 Chat Completions 线格式）
deepcode provider set my-ds --template deepseek --api-key

# 不交互的话，用环境变量（DEEPSEEK_API_KEY 会被自动识别）
deepcode provider set my-ds --template deepseek --api-key-env DEEPSEEK_API_KEY

# 2. 拉取并核对该连接可用的模型列表
deepcode provider models my-ds --refresh

# 3. 真机验证：不仅测连通，还测流式与工具调用
deepcode provider test my-ds --model deepseek-v4-pro --agent
```

然后在项目里启动：

```console
cd /path/to/your-project
deepcode --trust --connection my-ds --model deepseek-v4-pro
```

`--trust` 每个项目只需一次。之后直接 `deepcode`，用 `/model` 切换模型。

Web/Desktop 里等价操作是 **Settings → Models → Add provider → 选择 DeepSeek → 填入 Key → Save and verify model**。

### 2.5 三条硬约束（踩坑点）

1. **模型必须支持 tool calling。** Agent 的编辑、搜索、执行全靠工具调用，纯对话模型接进来只能聊天。`--agent` 参数就是专门验证这一点的，不做这步等于裸奔。
2. **`deepseek-r1` 的思考关不掉。** 目录里它的能力被标成 `mandatory=True`，`/effort off` 对它无效；而 `deepseek-v3` 是 `mandatory=False`，可以关。
3. **不要写错模型 ID 的期望值。** 只有 `deepseek-flash` / `deepseek-v4-*` 是 1M 上下文；`deepseek-v3` / `deepseek-r1` 是 128K。用错 ID 的话，`/context auto` 会按错误窗口规划压缩。

### 2.6 中转 / 私有网关

DeepSeek 只支持 OpenAI Chat Completions 风格，所以任何 OpenAI 兼容中转都能接：

```console
deepcode provider set relay --template custom \
  --api-base https://your-relay.example.com/v1 \
  --protocol openai_chat \
  --api-key-env RELAY_API_KEY

deepcode provider models relay --refresh
deepcode provider test relay --model deepseek-chat --agent
```

如果中转模型不在目录里，可在 `~/.deepcode/deepcode_config.json` 的 `providers.profiles.<id>.manualModels` 里手写 `id` / `contextWindow` / `maxOutputTokens`，否则上下文预算按未知模型处理。

---

## 3. 快速开始

```console
# 安装（需要 Python 3.12+；Windows 可用 winget install --id astral-sh.uv --exact）
uv tool install --python 3.12 deepcode-hku

# 初始化：创建 ~/.deepcode，默认访问级别设为 Ask
deepcode init

# 配置模型（以 DeepSeek 为例，其它模板同理）
deepcode provider set my-ds --template deepseek --api-key
deepcode provider test my-ds --model deepseek-v4-pro --agent

# 进入项目
cd /path/to/your-project
deepcode --trust --connection my-ds --model deepseek-v4-pro
```

四个入口：

| 客户端 | 启动命令 | 适用场景 |
|---|---|---|
| TUI | `deepcode`（或 `deepcode chat`） | 终端里干活，最轻 |
| Web | `deepcode web` | 浏览器里的完整工作台（diff、文件、终端、Goal 卡片） |
| Desktop | `deepcode desktop` | 同 Web 的本地应用，目前需从源码构建（Node 22 + Rust + Tauri 2 依赖） |
| Headless | `deepcode exec "<task>"` / `deepcode loop "<goal>"` | 脚本、CI；`--json` 输出 NDJSON 事件流 |

---

## 4. 代码架构分析

### 4.1 分层与依赖方向

`docs/P1_APP_SERVER_ARCHITECTURE.md` 里写死了依赖方向，代码也确实是这么分的：

```text
app_server ──▶ core/application ──▶ core/domain
                     │                  │
                     ├──▶ core/persistence ──▶ SQLite 投影
                     └──▶ core/sessions    ──▶ 权威 JSONL

CLI / TUI / MCP ──▶ Agent 内核 + core/sessions（独立适配层，不经过 application）

desktop/src/rpc ──▶ 生成的 TypeScript 契约
                          ▲
                          │
             protocol/app-server.schema.json
```

强制边界（对理解代码很重要）：

- `core/domain` **不 import** 任何 UI、传输、数据库、Provider、Agent、Workflow 实现。只有值对象和实体。
- `core/application` 拥有用例与事务编排，是唯一「知道完整业务流程」的层。
- `core/persistence` 只做 domain ↔ SQLite 映射，**不 import** application 和 app_server。
- `app_server` 只做 JSON-RPC 校验、调用 application、翻译稳定错误码，**不发 SQL**。
- `desktop/src/generated/app-server.ts` 由 `protocol/app-server.schema.json` 生成，**禁止手改**。

### 4.2 一次 Turn 的完整链路

以 Desktop/Web 发起一次对话为例（`docs/P2_AGENT_EXECUTION_ARCHITECTURE.md`）：

```text
turn/start
  ├─ 一个短 BEGIN IMMEDIATE 事务：
  │    校验 Project 已 trust → 重解析 workspace 边界
  │    拒绝已归档 Thread / 同 Thread 已有活跃 Turn
  │    插入 queued Turn + 已完成 UserMessage Item
  │    Thread 置为 running，写入投影事件
  ├─ 提交 → 发布事件 → 把执行任务塞进后台队列
  ▼
ExecutionRegistry（一个懒启动 asyncio loop + 有界信号量，默认 2 个并发 Turn）
  ▼
SessionRuntimeRegistry 取出该 Thread 长驻的 AgentSession
  ▼
先把用户消息 append 到权威 JSONL，再开始跑
  ▼
AgentRunner：模型 ↔ 工具循环（provider 流式返回 → 工具执行 → 结果回灌）
  ▼
TurnEventProjector：SQ/EQ 事件 → Item
     agent_message_delta → 流式 AssistantMessage Item（不逐次落盘）
     agent_message       → 完成态 Item
     文件写/改工具        → FileChange Item
     shell 命令          → CommandExecution Item
     其它工具            → ToolCall Item
  ▼
持久化投影 + 实时广播；活队列溢出时发 server.warning，客户端用 event/replay 追平
```

注意 `ExecutionRegistry` 的取舍：**stdio 请求线程永远不直接跑 provider 或工具**，避免阻塞 JSON-RPC；代价是多一层队列，取消逻辑要额外处理「还在排队就被取消」的情况。

### 4.3 模块职责与体量

按本仓库实测（`Get-Content` 行数统计，含空行与注释）：

| 模块 | 文件数 | 行数 | 职责 |
|---|---:|---:|---|
| `core/` | 266 | 75,289 | 领域模型、用例、Agent 内核、Provider、持久化、Skills、MCP、权限 |
| `tests/` | 277 | 66,440 | 单元 / 契约 / e2e / 持久化 / app_server 分层测试 |
| `desktop/` | 208 | 41,824 | Tauri 2 + React + Vite + Monaco，196 个 TS/TSX，5 个 Rust |
| `cli/` | 47 | 12,012 | 子命令、TUI（prompt_toolkit）、会话与自动化 CLI |
| `workflows/` | 18 | 9,378 | Paper2Code 多智能体编排 |
| `tools/` | 12 | 7,766 | 代码索引、PDF 下载/转换、文档切分、命令执行器 |
| `app_server/` | 32 | 6,286 | JSON-RPC 协议、stdio/WS 传输、后台服务宿主 |
| `prompts/` | 2 | 1,369 | 提示词模板 |
| `utils/` | 4 | 762 | 文件处理、LLM 工具函数、循环检测 |
| `eval/` | 8 | 641 | SWE-bench 预测/评估/报告 |

`core/` 内部再分层：

| 子目录 | 内容 |
|---|---|
| `core/domain/` | 纯领域对象：`thread`、`turn`、`item`、`project`、`approval`、`thread_goal`、`execution_permission` |
| `core/application/` | `turn_service`、`approval_service`、`execution_registry`、`automation_scheduler`、`llm_configuration_service`、`worktree_service` |
| `core/agent_runtime/` | `runner`（工具循环）、`compaction`/`microcompact`（上下文压缩）、`token_meter`、`evidence_ledger`（证据账本）、`goal_runtime`/`goal_verifier`（Goal 校验）、`repeat_guard` |
| `core/harness/` | `permissions`（三值权限引擎）、`sandbox`（平台沙箱）、`approval`、`tools/`（文件/补丁/搜索/Shell/Web/spawn_agent）、`hooks`、`skills`、`memory` |
| `core/providers/` | `registry`（15 条 ProviderSpec）、`catalog`（模型能力表）、`openai_compat`、`anthropic`、`openai_responses`、`oauth`、`egress`（出口域名限制） |
| `core/persistence/` | SQLite 仓储 + 迁移（`schema_migrations`） |
| `core/sessions/` | 权威 JSONL 会话存储 |
| `core/mcp/` | MCP 客户端/服务端、`presets.json` 目录、OAuth |
| `core/skills/` | Skill 协议、内建 8 个（frontend-design、mcp-builder、review-agent、security-*、skill-creator、webapp-testing） |

### 4.4 Provider 抽象：15 条声明怎么统一

`core/providers/registry.py` 共 15 条 `ProviderSpec`（14 家厂商 + `custom`）。它用声明式元数据描述一家厂商，而不是写 15 个 client：

```python
ProviderSpec(
    name="deepseek",
    keywords=("deepseek",),
    env_key="DEEPSEEK_API_KEY",
    display_name="DeepSeek",
    backend="openai_compat",
    endpoint_class="first_party",
    default_api_base="https://api.deepseek.com",
    thinking_style="thinking_type",
    thinking_default_on=True,
)
```

- `backend` 只有两个值：`openai_compat`（Chat Completions 线格式）和 `anthropic`（Messages 线格式）。`openai_responses` 不是第三种 backend，而是 `openai_compat` adapter 下的一种协议选项。新增厂商多数情况下**只是加一条 ProviderSpec**。
- `protocol` 默认 `auto`：字段保持 `auto`，真正的 adapter 由模板 `backend` 决定（`protocol_adapter()`）。用户也可以显式写 `openai_chat` / `openai_responses` / `anthropic_messages`；此时 `compat` 覆写字段必须与协议匹配，否则启动阶段直接报错。
- `thinking_style` 抽象了三种思考开关格式：`thinking_type`（DeepSeek / GLM）、`enable_thinking`（DashScope）、以及强制思考（R1/Grok）。
- `core/providers/model_compat.py` 处理各家的 wire 级差异，例如 DeepSeek 会拒绝「没有后续助手消息的上一轮 tool_call」。
- `egress` 允许把出口域名白名单写进配置（`allowedDomains: ["api.deepseek.com"]`），企业环境可用。

### 4.5 持久化：JSONL 为准，SQLite 为投影

这是整个架构里最关键的一个决定：

- **权威数据是 `SessionStore` 的 JSONL**（会话身份、可见对话记录）。CLI 和 Desktop 共用同一个 Session ID。
- **SQLite 是「可重建投影」**：`$DEEPCODE_HOME/state/deepcode.sqlite3`，包含 `projects / threads / turns / items / approvals / workflow_runs / artifacts / event_log / legacy_imports / schema_migrations`。
- 删掉 SQLite 文件**不能**删掉会话历史；下次 App Server 启动会从 JSONL 重建 Thread 与可见对话。
- 事件序号在单个 Thread 内单调递增；实时队列有界，溢出时让客户端走 `event/replay` 游标分页补齐。
- 流式 token 不逐次落盘，只记录 Item 的创建态与完成/中断态，避免写放大。

这也解释了排障时该看哪里：**对话不对 → 查 JSONL；界面状态不对 → 删 SQLite 让它重建**。

### 4.6 安全模型

| 机制 | 实现位置 | 说明 |
|---|---|---|
| 项目信任 | `cli/project_trust.py` | 未 trust 的目录不跑 Agent；项目自身配置不能自行提权 |
| 三值权限 | `core/harness/permissions.py` | `allow` / `ask` / `deny`，带不可覆盖的敏感路径黑名单、二维通配规则 |
| 访问档位 | `core/domain/execution_permission.py` | `Ask` / `Read only` / `Full access`；`deny` 规则在任何档位都生效 |
| OS 沙箱 | `core/harness/sandbox.py`、`windows_sandbox.py` | macOS seatbelt、Linux bubblewrap、Windows 独立实现；能力不足时优雅降级 |
| 审批 | `core/harness/approval.py`、`core/application/approval_service.py` | 持久化审批记录 + 内存 future，精确恢复被挂起的那个工具调用 |
| Hook 信任 | `core/harness/hooks/` | 仓库声明的 hook 必须人工批准，改动后需重新 `deepcode hooks approve` |
| 凭证隔离 | `core/providers/credentials.py`、`core/private_storage.py` | Key 存 `~/.deepcode/credentials.json`，仅属主可读，不回传给客户端，Shell 子进程看不到 |
| 出口限制 | `core/providers/egress.py` | 白名单域名，防止配置被改指到别的端点 |

### 4.7 Goal：可验证的「完成」

普通 Agent 的「做完了」只是一句话。DeepCode 的做法是：

1. Agent 先给出**可执行检查清单**，由用户确认；
2. 声称完成时，DeepCode 自己跑这份清单（`goal_runtime.py` / `goal_checks.py`）；
3. 可选开一个**独立 reviewer**（`goal_reviewer.py`）逐条给出证据；
4. 缺证据就打回，把「差什么」还给 Agent；
5. Goal 有时间上限、token 预算、卡死/重复自停、重启可恢复。

配套的 `evidence_ledger.py` 记录每次工具调用与检查结果——这是「done means checked」能成立的原因，也是它区别于「让 Agent 显得很忙」的地方。

### 4.8 Paper2Code 多智能体

老本行还在 `workflows/` 里，`agent_orchestration_engine.py` 编排七类角色：

1. Research Analysis Agent —— 论文内容抽取
2. Workspace Infrastructure Agent —— 环境/目录骨架
3. Code Architecture Agent —— 设计规划
4. Reference Intelligence Agent —— 参考实现检索
5. Repository Acquisition Agent —— 仓库获取
6. Codebase Intelligence Agent —— 代码关系分析
7. Code Implementation Agent —— 代码合成

入口在 Desktop/Web 里用 `/paper`，对应文档 `docs/paper2code.md`（有中文版 `paper2code.zh-CN.md`）。`eval/swebench/` 另有一套 SWE-bench 的 predict/evaluate/report 工具链。

### 4.9 桌面端

`desktop/` 是 Tauri 2 + React + Vite + TypeScript，用 Monaco 做代码工作台：

- `desktop/src-tauri/`：Rust 侧，负责启动 Python sidecar、诊断、窗口能力
- `desktop/src/rpc/`：两种运行时（浏览器 / Tauri），共享 `contracts.ts`
- `desktop/src/generated/app-server.ts`：由协议 JSON Schema 生成，保证前后端契约不漂移
- `desktop/e2e/`：Playwright 端到端测试

源码构建需要 Node 22+、Rust stable 和 Tauri 2 平台依赖，首次构建要几分钟。

---

## 5. 目录速查

```text
deepcode.py             # 单一入口，按 argv[1] 分发到 17 个子命令
core/                   # 领域 + 用例 + Agent 内核 + Provider + 持久化
app_server/             # JSON-RPC 协议、stdio/WS 传输、后台服务宿主
cli/                    # 子命令实现；cli/tui/ 是终端界面
desktop/                # Tauri 2 + React 客户端
workflows/              # Paper2Code 多智能体编排
tools/                  # 代码索引、PDF、文档转换等重型工具
prompts/                # 提示词模板
eval/swebench/          # SWE-bench 评测
protocol/               # app-server.schema.json（前后端契约源头）
docs/                   # 指南、架构文档（P1~P6）、集成说明
tests/                  # 277 个测试文件，含契约与 e2e
```

用户数据目录：

```text
~/.deepcode/
├── deepcode_config.json        # 配置（providers、agents、goal、logger…）
├── credentials.json            # API Key，仅属主可读
├── state/deepcode.sqlite3      # 可重建的 SQLite 投影
├── sessions/                   # 权威 JSONL 会话记录
└── logs/
<project>/.deepcode/memory/     # 项目级长期记忆
```

也可以用 `DEEPCODE_HOME` 整体改家目录。

---

## 6. 设计取舍评价

**做得好的地方**

- 依赖方向是**被文档化并被测试守住**的（`tests/contract/`），不是口头约定。
- Provider 层用数据（`ProviderSpec` + 模型目录）而不是代码分叉，加一家厂商成本很低。
- 「JSONL 权威 + SQLite 投影」这套组合，正确解决了「多客户端 + 可重建状态」的一致性问题。
- Goal 的完成校验有真正的执行与证据，而不是让模型自证。
- 测试体量（66k 行）接近核心代码体量（75k 行），在同类开源 Agent 项目里属于高投入。

**需要预先知道的代价**

- **复杂度不低。** 266 个 Python 文件 + 196 个 TS 文件，想改核心行为要先读懂 P1/P2 两份架构文档，否则很容易在错误的层动手。
- **平台能力不对等。** Linux 有 bubblewrap，macOS 有 seatbelt，Windows 是独立实现；README 只对 Linux 的 `bwrap` 做出承诺，跨平台沙箱强度需要自己验证。
- **DeepSeek 模板未声明 prompt caching**（`supports_prompt_caching=False`），长会话成本主要靠 `microcompact` / `/compact` 控制，而不是靠缓存命中。
- **Desktop 没有现成安装包**，必须源码构建，首次上手成本高于 TUI。
- 双写模型虽然正确，但排障时必须区分「JSONL 不对」和「投影不对」，混在一起查会浪费时间。

---

## 7. 常用命令速查

```console
# 初始化与版本
deepcode init
deepcode --version

# 模型连接
deepcode provider list
deepcode provider set my-ds --template deepseek --api-key
deepcode provider models my-ds --refresh
deepcode provider test my-ds --model deepseek-v4-pro --agent

# 启动
deepcode                                  # TUI
deepcode --trust --connection my-ds --model deepseek-v4-pro
deepcode web                              # 浏览器工作台
deepcode desktop                          # 桌面端（需源码构建）
deepcode exec "跑一遍测试并汇报失败原因" --json
deepcode loop "把所有 lint 错误修掉"

# 服务
deepcode service status
deepcode service logs --lines 100
deepcode service stop --drain --timeout 60
deepcode service install --at-login

# 会话 / 扩展
deepcode session delete <id>
deepcode skill list
deepcode plugin list
deepcode hooks status
deepcode mcp list
deepcode mcp serve

# 自动化
deepcode schedule ...
deepcode automation ...
```

TUI 内常用斜杠命令：`/model`、`/effort`、`/context 64k`、`/goal <objective>`、`/resume`、`/compact`。`/paper`（Paper2Code）在 Desktop / Web 客户端中使用。

---

## 8. 文档索引

| 主题 | 文档 |
|---|---|
| 第一次上手 | [docs/guide/getting-started.md](docs/guide/getting-started.md) |
| 模型与 Provider | [docs/guide/models.md](docs/guide/models.md) |
| Provider 连接参考 | [docs/PROVIDER_CONNECTIONS.md](docs/PROVIDER_CONNECTIONS.md) |
| 配置项与环境变量 | [docs/guide/configuration.md](docs/guide/configuration.md) |
| 权限、沙箱、数据 | [docs/guide/security.md](docs/guide/security.md) |
| Goal 与自动化 | [docs/guide/goals-and-headless.md](docs/guide/goals-and-headless.md) |
| 会话与压缩 | [docs/guide/sessions.md](docs/guide/sessions.md) |
| Skills 与记忆 | [docs/guide/skills-and-memory.md](docs/guide/skills-and-memory.md) |
| MCP 集成 | [docs/integrations/MCP.md](docs/integrations/MCP.md) |
| 后台服务 | [docs/SERVICE_RUNTIME.md](docs/SERVICE_RUNTIME.md) |
| 架构 P1：应用核心与 App Server | [docs/P1_APP_SERVER_ARCHITECTURE.md](docs/P1_APP_SERVER_ARCHITECTURE.md) |
| 架构 P2：Agent 执行 | [docs/P2_AGENT_EXECUTION_ARCHITECTURE.md](docs/P2_AGENT_EXECUTION_ARCHITECTURE.md) |
| 架构 P3/P4：桌面与代码工作台 | [docs/P3_DESKTOP_RUNTIME_ARCHITECTURE.md](docs/P3_DESKTOP_RUNTIME_ARCHITECTURE.md) · [docs/P4_CODE_WORKBENCH_ARCHITECTURE.md](docs/P4_CODE_WORKBENCH_ARCHITECTURE.md) |
| 架构 P5：Paper2Code | [docs/P5_PAPER2CODE_ARCHITECTURE.md](docs/P5_PAPER2CODE_ARCHITECTURE.md) |
| 故障排查 | [docs/guide/troubleshooting.md](docs/guide/troubleshooting.md) |

---

## 9. 许可证

MIT License，Copyright © 2025 Data Intelligence Lab at The University of Hong Kong。详见 [LICENSE](LICENSE)。

引用：

```bibtex
@misc{li2025deepcodeopenagenticcoding,
  title         = {DeepCode: Open Agentic Coding},
  author        = {Zongwei Li and Zhonghang Li and Zirui Guo and Xubin Ren and Chao Huang},
  year          = {2025},
  eprint        = {2512.07921},
  archivePrefix = {arXiv},
  primaryClass  = {cs.SE},
  url           = {https://arxiv.org/abs/2512.07921}
}
```
