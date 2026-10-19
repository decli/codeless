# AI Coding Agent 行业调研（面向 codeless 对标 Claude Code / OpenAI Codex）

- 调研日期：2026-10-19
- 范围：终端/IDE 类 coding agent、带实时预览的 App Builder、国内同类产品；成熟 coding agent 能力体系；常用基准；弱模型或用户可配置模型下的设计要点。
- 方法：约 30 次检索与官方文档抓取，优先采用官方文档，第三方资料在文中注明。本轮未能在可靠来源中确认的内容标注"未核实"；来自既有知识、本轮未重新查证的内容标注"本轮未复核"。
- 时效：本领域迭代很快，版本号、模式名称、榜单分数均为时间点数据，引用前应再次核对。

## 0. 结论摘要

1. 头部产品的 agent 循环高度同构：收集上下文、行动（编辑/执行）、验证，循环直至完成，用户可随时打断或插话（Claude Code 官方文档对此有明确描述）。产品差异主要在 harness：工具设计、上下文管理、验证闭环和沙箱，循环本身差别不大。
2. 文件编辑的主流做法是"精确字符串替换 + 唯一性校验"（Claude Code `Edit`、Anthropic `str_replace_editor`）或自定义 patch 格式（Codex `apply_patch`）。整文件重写只用于新文件或兜底；Aider 会按模型选择编辑格式。
3. 执行环境正从浏览器内运行时（Bolt 的 WebContainers、v0 早期预览）转向每会话一台隔离 VM 或容器（v0 Vercel Sandbox、Cursor Cloud Agents、Devin、Codex Cloud、Copilot 的 Actions 环境）。codeless 的 Docker 沙箱与这一方向一致，并且天然支持 Python。
4. App Builder 的核心竞争力在运行时反馈闭环：读取控制台和网络日志（Lovable）、用真实浏览器自测（Replit App Testing）、启动 dev server 后验证运行结果（v0）、把截图和录像附到 PR（Cursor Cloud Agents）。
5. 长任务能否完成，很大程度取决于上下文工程：项目记忆文件（AGENTS.md / CLAUDE.md / GEMINI.md）、工具输出截断并落盘、先清理旧工具输出再做摘要压缩、子代理隔离上下文、MCP 工具定义按需加载。
6. 模型策略分两派。一派由厂商固定或限定模型族（Claude Code、Gemini CLI、Devin、Amp、多数 App Builder）；另一派让用户配置（Aider、Cline、OpenHands、bolt.diy、Codex 的 `model_providers`）。Cursor 官方明确会按每个模型调优指令和工具，用户可配置模型的产品都需要类似的模型适配层。
7. OpenAI 已于 2026-02 以数据污染为由停止报告 SWE-bench Verified，目前更常引用 SWE-bench Pro 和 Terminal-Bench 2.0。同一模型换用不同 harness 后分数差异明显，scaffold 值得持续投入。
8. 对 codeless 而言，模型不可控，竞争力来自 harness 的鲁棒性：容错的工具调用解析、多级编辑回退、由 harness 强制执行的结构化验证（进程、端口、HTTP、浏览器控制台），以及按模型能力分级的适配层。

## 1. 产品图谱

### 1.1 总览

| 产品 | 类别 | 代码执行位置 | 实时预览 | 模型 | 开源 |
|---|---|---|---|---|---|
| Claude Code | 终端/IDE/桌面/Web agent | 本地；Anthropic 托管云 VM 或自托管环境；Remote Control | 无内置预览，可借 Claude in Chrome 操作浏览器 | 仅 Claude 系列，`/model` 切换；可经 Bedrock 等云平台接入 | 否 |
| OpenAI Codex | CLI + IDE 扩展 + Cloud | 本地 OS 级沙箱；云端隔离容器 | 无 | GPT 系列；`config.toml` 可配 `model_providers`；`--oss` 接 Ollama/LM Studio | CLI 开源（openai/codex） |
| Cursor | AI IDE + Cloud Agents | 本地；Cloud Agent 每任务独立 VM | Browser 工具截图/交互；云端可接管远程桌面 | 多模型，按任务选择 | 否 |
| GitHub Copilot coding agent | 异步云 agent | GitHub Actions 临时环境 + 出口防火墙 | 无（产出 PR） | 未核实 | 否 |
| Gemini CLI | 终端 agent | 本地，可选 `--sandbox` | 无 | Gemini 系列 | 是（Apache 2.0） |
| Aider | 终端结对编程 | 本地 | 无 | 任意（经 LiteLLM，本轮未复核） | 是 |
| Cline | VS Code 扩展 | 本地 | 浏览器工具（computer use） | 任意供应商，Plan/Act 可分设模型 | 是 |
| OpenHands | 开源 agent 平台/SDK | Docker 等可插拔 runtime | 沙箱内含 Chromium | 任意（LiteLLM，含无函数调用模型） | 是（MIT，二手资料） |
| Devin | 云端自主工程师 | 每会话云 VM；Outposts 可在客户基础设施执行 | 本轮未复核 | 厂商选定 | 否 |
| Amp | agent + 云机器 Orbs | 本地 CLI / 云端 Orb | 未核实 | 厂商按任务组合多模型 | 否 |
| Kiro（AWS） | spec 驱动 AI IDE | 本地 | 未核实 | 未核实 | 否 |
| Bolt.new | 浏览器内 App Builder | 浏览器内 WebContainers | 是 | 默认 Claude；开源版 bolt.diy 可接任意模型 | bolt.diy 开源 |
| Lovable | App Builder | 平台托管（细节未核实） | 是 | 未核实 | 否 |
| v0（Vercel） | App Builder | 每个 chat 一台 Vercel Sandbox VM（来源为文档镜像） | 是 | 未核实 | 否 |
| Replit Agent | 云 IDE + App Builder | Replit 云工作区 | 是，agent 用真实浏览器自测 | 厂商选定 | 否 |
| Trae / SOLO（字节） | AI IDE + 全流程模式 | 本地 IDE；SOLO 有独立应用 | 内置浏览器预览 | 国内版接国产模型，国际版接 Claude/Gemini/GPT | 否 |
| CodeBuddy（腾讯） | AI 编程工具 | 未核实 | 未核实 | 未核实 | 否 |

### 1.2 终端/IDE 类 coding agent

**Claude Code（Anthropic）**
- 定位：运行在终端、IDE、桌面、Web、Slack、CI 中的通用 agentic harness，核心循环在各入口一致。
- 循环与工具：官方将循环描述为"收集上下文 → 采取行动 → 验证结果"。内置工具包括 `Read`（带行号，支持图片/PDF/notebook）、`Edit`（精确字符串替换）、`Write`、`Bash`（可 `run_in_background`）、`Monitor`（后台命令的每行输出作为事件回灌）、`LSP`（编辑后的类型错误、跳转定义）、`WebFetch`/`WebSearch`、`Agent`（子代理）、`TaskCreate/TaskUpdate`（任务清单，取代旧 `TodoWrite`）、`AskUserQuestion`、`EnterPlanMode`、`EnterWorktree`、`Skill`、`ToolSearch`。新版本在 macOS/Linux/WSL 上默认不提供 `Glob`/`Grep`；`Edit` 也认可通过 `cat`/`grep`/`sed -n` 等命令读到的内容。
- 编辑语义：`old_string` 必须精确出现且只出现一次，不做正则或模糊匹配；多处匹配时要么补充上下文，要么用 `replace_all`；默认要求先读后改；会拒绝改写非 UTF-8 文件，以免损坏编码。
- 沙箱与权限：权限模式包括 Auto（后台分类器判断风险并拦截，新版本的默认起始模式）、Manual、Accept edits、Plan，另有 bypass。规则用 `ToolName(specifier)` 格式写在 settings 中；hooks 可以在 PreToolUse 等事件上进一步收紧策略。
- 可逆性：每次编辑前对文件做快照，按两次 `Esc` 可回退；检查点独立于 git，只覆盖文件改动，不覆盖数据库或部署等外部副作用。
- 上下文：CLAUDE.md（也可读 AGENTS.md）；auto memory（MEMORY.md 每次会话加载前 200 行或 25KB）；接近上限时先清理旧工具输出，再摘要；支持 `/compact <focus>` 和 CLAUDE.md 中的 "Compact Instructions"；MCP 工具定义默认延迟加载。
- 会话：对话以 JSONL 存于 `~/.claude/projects/`，支持 `--continue`/`--resume`/`--fork-session`，可配合 git worktree 并行。
- 模型：仅 Claude（Sonnet/Opus 等），可通过 Amazon Bedrock、Google Cloud、Microsoft 等云平台部署（工具文档提及）。
- 差异点：扩展体系最完整（skills、hooks、MCP、plugins、子代理、agent teams、Workflow 编排、Agent SDK、`-p` headless）；Auto 模式用分类器替代逐条审批。

**OpenAI Codex（CLI + IDE + Cloud）**
- 定位：OpenAI 的 coding agent，同一套能力覆盖本地 CLI、IDE 扩展和云端任务（`codex cloud` 可提交任务到云环境，再把结果 apply 回本地）。
- 循环与工具：shell 执行加 `apply_patch` 自定义 patch 编辑格式（本轮未复核当前细节）；支持子代理委派；`/init` 生成 AGENTS.md；`/model` 选择模型和推理强度；`codex mcp` 管理 MCP；`--image` 或粘贴图片作为输入。
- 沙箱：`sandbox_mode` 取值 `read-only`、`workspace-write`、`danger-full-access`。官方页只列出后两者，`read-only` 来自第三方资料。第三方资料称默认 `workspace-write` 不联网。OS 级实现为 macOS Seatbelt、Linux Landlock/seccomp（本轮未复核）。
- 审批：`approval_policy` 取值 `on-request`、`never`，或 `granular{sandbox_approval, rules, mcp_elicitations, request_permissions, skill_approval}`。官方称 `untrusted` 已退役。`approvals_reviewer = "auto_review"` 由审查子代理代替人工审批。
- 云端：第三方资料称每个任务运行在 OpenAI 托管的隔离容器中，agent 阶段默认断网。
- 上下文：AGENTS.md 从全局到各级目录串联，`project_doc_max_bytes` 限制读取量（第三方称默认 32 KiB），`project_doc_fallback_filenames` 可指定备用文件名。
- 模型：`model_providers` 可配 `base_url`/`env_key`/`wire_api`/headers/重试参数，`openai`、`ollama`、`lmstudio` 为保留 ID；内置 `amazon-bedrock`。官方示例中 `wire_api` 为 `responses`；第三方称 2026-02 起已移除 Chat Completions 支持（未核实）。如果属实，只提供 `/chat/completions` 的 OpenAI 兼容端点将无法直接接入。
- 其他：`codex exec` 用于 CI/脚本，`codex resume` 恢复会话，hooks 写在 `hooks.json` 或 `config.toml`（如 `PreToolUse`）。

**Cursor（Agent / Cloud Agents）**
- Agent 工具：文件名/目录/grep 搜索、读文件（含图片）、编辑并自动应用、终端、Browser（截图、交互、验证视觉变化）、图片生成、提问、拉取 Rules；工具调用次数不设上限。
- 检查点：重大改动前快照，本地存储，独立于 git，只恢复文件，不恢复对话。
- 转向：Enter 排队，"Send now" 在下一次工具调用时转向；CLI 中 Enter 转向、再按一次打断；`/goal` 配合 `/loop` 处理长目标。
- Cloud Agents（原 Background Agents）：每个 agent 独立 VM，包含仓库、依赖、密钥和网络；自测后把视频、截图和日志附到 PR；可接管远程桌面；可从 IDE、Web/移动端、Slack/GitHub/Linear 的 @Cursor 触发，也可定时或按事件自动触发。二手资料称 2026-09 起可运行在 Vercel Sandbox。
- 模型：按任务选择模型；官方称"为每个支持的模型调优指令与工具"。多家厂商模型加自研 Composer 的组合本轮未复核。

**GitHub Copilot coding agent**
- 指派 issue 后在 GitHub Actions 驱动的临时开发环境中工作，最终产出 PR。环境可用 `.github/workflows/copilot-setup-steps.yml` 预装依赖。
- 出口防火墙默认开启，带推荐白名单；被拦截的请求会在 PR 或评论中标注地址和命令。防火墙只作用于 agent 的 Bash 工具，不覆盖 MCP 和 setup steps。
- 差异点：与 issue/PR/Actions 原生集成，适合异步批量任务。模型选择未核实。

**Gemini CLI（Google）**
- 开源（Apache 2.0），采用 ReAct 式循环，内置文件、shell、Google Search grounding、web fetch 工具，可用 MCP 扩展；项目记忆文件为 GEMINI.md。
- 检查点：默认关闭，需在 settings 中开启。执行 `write_file`/`replace` 等修改前，在 `~/.gemini/history/<project_hash>` 的影子 git 仓库中快照，同时保存对话和待执行的工具调用。`/restore` 同时回滚文件和对话，并重新提出原工具调用。
- 沙箱：`--sandbox` 隔离执行（Docker/Podman/Seatbelt 的具体实现本轮未复核）。

**Aider**
- 终端结对编程工具，与 git 深度集成（本轮未复核当前细节）。
- 编辑格式：`whole`（整文件，最易用但耗 token）、`diff`（search/replace 块）、`diff-fenced`（路径放进围栏内，主要配合 Gemini）、`udiff`（简化 unified diff，曾用于抑制 GPT-4 Turbo 的"偷懒占位"）、`editor-diff`/`editor-whole`（architect 模式下的编辑模型专用）。会按模型自动选择，也可用 `--edit-format` 覆盖。
- Architect 模式：一个模型出方案，另一个模型生成格式化编辑。
- 维护 Aider Polyglot 基准，同时报告正确率和"格式良好率"。

**Cline / Roo Code**
- Cline：VS Code 内的开源 agent。Plan 模式只读并产出计划，Act 模式执行（v3.2 引入）。每一步后自动给工作区做检查点，可回滚；浏览器动作基于 computer use；每个动作可配置人工批准；支持 MCP。
- 模型：Anthropic、OpenAI、OpenRouter、Ollama、LM Studio 等，Plan 和 Act 可设不同模型。
- Roo Code：Cline 的分支，以自定义模式（Code/Architect/Debug 等）著称。本轮未核实其现状。

**OpenHands（All Hands AI）**
- 开源平台加 Software Agent SDK（arXiv 2511.03690），分 core/tools/workspaces/server 层，本地默认运行，可切换到远程沙箱，支持 MCP 和确定性回放。
- 默认 agent 为 CodeAct，以 Python/bash 代码作为统一动作空间。runtime 为 Docker 沙箱（bash + IPython + Playwright Chromium），另有 Remote/Modal/Daytona/Local 等实现（二手资料）。
- 模型：经 LiteLLM 接入 100+ 供应商，二手资料称支持无函数调用能力的模型。这与 codeless 的可配置模型场景最接近。

**Devin（Cognition）**
- 云端自主工程师，每会话一台云 VM。Cognition 称采用 VM 级隔离（替代容器）、hypervisor 级状态快照，并可编排数千并发会话（二手转述）。
- 2026 年新增 CLI 控制和 SSH 进入云 VM；Devin Outposts（2026-07 alpha，基于 Modal）让推理留在 Cognition 云端，代码在客户基础设施上执行。支持子代理。模型由厂商选定。

**Amp**
- 官方定位为"面向前沿、无向后兼容包袱"的 agent。厂商按任务组合多个模型，未提供明确的用户自选模型说明。Orbs 是每个 thread 一台云机器，关掉电脑后 agent 仍可继续工作。thread 可保存和分享。有 CLI、Web、macOS/iOS 等入口。
- Oracle、Librarian 等专用子代理在既有资料中常被提及，本轮未在官方页面核实。

**Kiro（AWS）**
- spec 驱动开发：把单条需求展开为用户故事（requirements），再生成设计文档和实现任务清单。steering 文件提供持久上下文；hooks 在保存、创建、删除文件或手动触发时在后台运行 agent 任务；支持按工具权限划定范围的自定义子代理。模型未核实。

### 1.3 带实时预览的 App Builder

**Bolt.new（StackBlitz）**
- LLM（默认 Claude）生成代码，WebContainers 在浏览器内运行 Node.js 和 dev server，agent 可以装包、跑命令、读错误并修复，用户看着预览更新（二手资料）。
- 官方开源版 bolt.diy 支持 OpenAI、Anthropic、Ollama、OpenRouter、Gemini、LM Studio、Mistral、xAI、DeepSeek、Groq 等，可扩展到 Vercel AI SDK 支持的任意模型。
- 局限：二手资料称预览与生产行为不一致（临时存储、凭据留在平台内）；WebContainers 对 Python 等原生运行时支持有限（本轮未复核）；用户在没有明确目标时反复要求"just fix it"会陷入高成本的错误循环。二手资料称旧版 v1 Agent 在 2026 年下线，由 Claude Agent 取代。

**Lovable**
- 默认模式一步生成；Agent Mode（2025-06 beta）为多步执行：检索代码库、按需读文件、检查日志和网络请求来定位错误、搜索网络文档、生成或编辑图片，过程中自动修复问题，最后给出总结。按用量计费。
- 后端常与 Supabase 集成（本轮未复核）。2026 年的工具集未核实。

**v0（Vercel）**
- 文档镜像称每个 chat 运行在独立的 Vercel Sandbox VM 中，承载项目、dev server 和 agent 工具，实时预览也由该 VM 提供。这一方案取代了旧的浏览器内预览，原因是旧方案无法运行服务端代码、API 路由和真实数据库连接（来源为 v0.build 子域镜像，未在主站核实）。
- v0 API 中，agent 会启动 dev server、返回预览 URL 并验证运行中的代码，据此发现错误并尝试修复。

**Replit Agent**
- Agent 3（2025-09）推出 App Testing：agent 用真实浏览器测试自己的应用，在反思循环中发现并修复问题；可自主运行最长 200 分钟，配合 Live Monitoring；App Testing 支持 Full Stack JavaScript 和 Streamlit Python 应用；能构建定时运行或接入 Slack/Telegram 的 agent 和自动化。
- Agent 4（2026-03，官方 changelog）新增设计画布（Infinite Canvas）、规划、并行子代理和协作。App Testing 是否延续本轮未核实。检查点与回滚本轮未复核。

### 1.4 国内产品（只列可部分确认的信息）

| 产品 | 已确认信息 | 未核实 |
|---|---|---|
| Trae（字节） | IDE 模式与 SOLO 模式可切换，SOLO 定位为覆盖需求、开发、测试、部署的全流程；内置浏览器预览 Web 应用；国内版接豆包/Kimi/Qwen/GLM/DeepSeek，国际版接 Claude/Gemini/GPT；支持 MCP 自定义智能体（掘金、DataCamp 等二手资料） | SOLO 于 2026-03-31 推出独立应用并演进为 TRAE Work；与豆包 MarsCode 的关系 |
| CodeBuddy（腾讯） | 腾讯云官方文档定位为一站式 AI 编程工具，覆盖从需求文档到部署 | 执行模型、预览、模型可配置性；2026 年价格调整 |
| Qoder / 通义灵码（阿里） | 阿里系 AI 编程产品 | 第三方称 2026-05 通义灵码品牌升级为 Qoder，以及市场份额数据 |
| 秒哒（百度） | 面向无代码应用生成 | 自媒体称 2026-05 升级至 V3.0，强化多智能体协作 |

### 1.5 横向观察

- 预览形态：浏览器内运行时（Bolt）启动快、成本低，但跑不了服务端和原生依赖；VM/容器方案（v0、Replit、Cursor Cloud、Devin）能完整运行后端。codeless 采用 Docker，与后者一致。
- 自修复的信号来源：构建/运行错误（Bolt）、日志和网络请求（Lovable）、真实浏览器交互（Replit、Cursor Browser）、测试结果（Claude Code、Codex 等终端 agent）。信号越接近真实用户行为，修复越有效。
- 模型可配置的产品几乎都是开源或自托管的（Aider、Cline、OpenHands、bolt.diy、Codex 自定义 provider）。商业 App Builder 普遍不开放模型选择，以此保证质量下限。

## 2. 成熟 coding agent 能力体系

优先级列是针对 codeless 的建议：P0 为必须具备，P1 为对标所需，P2 为差异化或后续能力。

| 能力 | 要点 | 代表产品 | 优先级 |
|---|---|---|---|
| Agent 循环与工具调用 | 模型自主选择工具，观察结果后决定下一步；脚手架保持精简，把控制权交给模型 | 全部；Anthropic SWE-bench scaffold | P0 |
| 文件读取 | 带行号；大文件按 offset/limit 分页；支持图片、PDF | Claude Code `Read`、Cursor | P0 |
| 精确编辑 | `old_string` 唯一匹配替换，失败时返回可操作的错误；或 patch/diff 格式；整文件写入兜底 | Claude Code `Edit`、Codex `apply_patch`、Aider 多格式 | P0 |
| 代码搜索 | glob/grep/ripgrep；仓库地图；LSP 跳转与引用 | Claude Code、Cursor、Aider repo map | P0 |
| Shell 执行 | 默认超时加上限，输出截断，失败输出保留头尾 | Claude Code `Bash`、Codex | P0 |
| 后台进程与日志流 | dev server/watch 放后台；超时命令转后台而非直接杀掉；日志按行回灌 | Claude Code `run_in_background`/`Monitor` | P0 |
| 规划与任务清单 | 显式 todo/task 工具；Plan 模式只读探索并出方案 | Claude Code Tasks/Plan、Cline Plan/Act、Kiro spec、Cursor Plan | P1 |
| 项目记忆文件 | AGENTS.md/CLAUDE.md/GEMINI.md 分层加载，有大小上限；`/init` 自动生成 | Claude Code、Codex、Gemini CLI、Cursor Rules、Kiro steering | P0 |
| 跨会话自动记忆 | agent 自行沉淀偏好和经验，下次会话加载 | Claude Code auto memory | P2 |
| 上下文压缩 | 先清理旧工具输出，再摘要；可指定保留重点；防止反复压缩的抖动 | Claude Code、Anthropic 平台 compaction/context editing | P0 |
| 工具输出截断与落盘 | 超长输出写入文件，只回传预览和路径，需要时再读 | Claude Code `Bash` | P0 |
| 工具与知识按需加载 | MCP 工具定义延迟加载（tool search）；skills 只先暴露描述 | Claude Code | P2 |
| 沙箱隔离 | OS 级沙箱、容器或 VM；工作区可写，其余只读 | Codex（Seatbelt/Landlock）、Gemini `--sandbox`、OpenHands Docker、Devin VM | P0 |
| 权限与审批模式 | 只读/编辑自动/全自动分级；规则白名单；分类器或审查子代理代替人工审批 | Claude Code Auto、Codex `auto_review`、Cline 逐步批准 | P1 |
| 网络出口控制 | 默认断网或白名单，被拦截时明确告知 | Copilot 防火墙、Codex Cloud、Cursor 网络策略 | P1 |
| 检查点与回滚 | 每次编辑前快照（影子 git 或文件快照），独立于用户 git；可同时回滚对话 | Claude Code、Cursor、Cline、Gemini CLI `/restore`、Replit | P0 |
| 实时预览 | 自动探测端口和启动命令，反向代理到前端 iframe | Bolt、v0、Lovable、Replit、Trae | P0 |
| 运行时错误回灌与自修复 | 构建错误、进程崩溃、HTTP 5xx、浏览器 console 和网络错误自动成为下一轮输入，有重试预算 | Lovable、Replit、Bolt、v0 | P0 |
| 静态验证 | 编辑后做 lint、类型检查和 LSP 诊断，跑测试 | Claude Code `LSP`、Aider（本轮未复核） | P1 |
| 浏览器验证 | 截图、交互、录像，用于视觉回归和端到端自测 | Replit App Testing、Cursor Browser/Cloud、OpenHands、Cline | P1 |
| 中途转向与打断 | 立即中断；或排队消息在下一次工具调用后注入同一轮 | Claude Code、Cursor | P0 |
| 主动澄清 | 多选式提问，不阻塞其他工作 | Claude Code `AskUserQuestion`、Cursor | P1 |
| 子代理与并行 | 独立上下文窗口，只回传摘要；按 worktree 或 VM 并行 | Claude Code `Agent`/Workflow、Codex、Replit Agent 4、Kiro、Devin | P2 |
| MCP | 接入外部工具和数据源 | 几乎全部 | P1 |
| Skills / 斜杠命令 / Hooks | 可复用工作流；生命周期 hooks（如 PreToolUse）做确定性策略 | Claude Code、Codex hooks、Kiro hooks | P2 |
| 多模型 | 运行时切换；规划与编辑分设模型；按模型调优提示词 | Cursor、Cline、Aider architect、Codex providers | P0 |
| Headless/CI/SDK | 非交互运行、结构化输出、可编程嵌入 | `claude -p`/Agent SDK、`codex exec`、Copilot | P1 |
| 成本与用量 | 按会话/轮次统计 token 和费用；按用量计费 | Claude Code `/context` 与费用统计、Lovable 用量计费 | P1 |
| 会话持久化 | 恢复、分叉、搜索历史；跨设备 | Claude Code JSONL、`codex resume`、Amp threads | P0 |
| 图片输入 | 截图或设计稿作为输入；读取图片文件 | Codex `--image`、Claude Code `Read`、Cursor | P1 |
| 云端异步执行 | 关掉客户端后仍继续运行，结果以 PR 或通知交付 | Cursor Cloud、Codex Cloud、Copilot、Devin、Amp Orbs | P2 |

### 2.1 关键参数参考（Claude Code 官方文档）

| 项 | 默认值 |
|---|---|
| Bash 超时 | 默认 2 分钟，上限 10 分钟（模型可按命令传 `timeout`） |
| Bash 成功输出 | 约 30,000 字符内联；超出则落盘，回传路径和前 2,000 字符预览 |
| Bash 失败输出 | 约 10,000 字符，取头尾，不落盘 |
| 后台命令时限（无人值守会话） | 默认 30 分钟，最长 2 小时；超时后告知模型，可以更长的 timeout 重启 |
| 前台命令超时 | 自动转入后台继续运行（`sleep` 开头的命令除外） |
| Read 单次 | 最多 25,000 tokens；整文件超过 256KB 时要求分页或改用搜索 |
| Auto memory | MEMORY.md 前 200 行或 25KB |
| Codex AGENTS.md | `project_doc_max_bytes` 可配（第三方称默认 32 KiB） |

这些数值可直接作为 codeless 工具层的初始参数，再按所配模型的上下文窗口等比缩放。

## 3. 常用基准

| 基准 | 内容 | 现状与注意事项 |
|---|---|---|
| SWE-bench Verified | 从 SWE-bench 中人工筛选的 500 个 Python 仓库 issue 修复任务，用隐藏测试判定 | OpenAI 2026-02-23 宣布停止报告：审计最难的 138 题中 59.4% 存在测试或描述缺陷，多个前沿模型能凭记忆复现原始补丁，即存在污染。其他厂商仍在引用 |
| SWE-bench Pro（Scale AI） | 1,865 个任务，41 个仓库，覆盖 Python/Go/TypeScript/JavaScript，难度更高 | 同一模型分数远低于 Verified；二手资料称 2026-05 有审计指出约三分之一的判分有误（未核实） |
| Terminal-Bench 2.0 | 89 个终端环境中的真实任务（编译、运维、数据处理等），官方 harness 为 Harbor，默认 scaffold 为 Terminus 2 | 2026-04 快照中榜首约 82%（Codex + GPT-5.5，第三方聚合站）；Snorkel 统计 142 个提交的中位数约 44%。榜单同时对比"模型 × harness"组合 |
| Aider Polyglot | 225 道 Exercism 难题，覆盖 C++/Go/Java/JS/Python/Rust | 同时报告正确率和编辑格式良好率；测的是"模型经由 Aider"的表现，不是 Aider 与其他工具对比 |
| SWE-bench Multimodal / SWE-Lancer 等 | 前者为带视觉信息的前端 issue，后者为真实外包任务 | 本轮未复核 |
| App 生成类 | 尚无公认的自动化标准基准；业界多用人工偏好评测（如 WebDev Arena，本轮未复核） | codeless 需要自建任务集（见第 5 节） |

### 3.1 影响成功率的因素

1. 模型能力仍是最大的单一因素：Anthropic 在同一 scaffold 下，Claude 3 Opus 得 22%，3.5 Sonnet（new）得 49%。
2. Harness 质量：Terminal-Bench 显示同一模型换 harness 后分数差异明显；二手资料称某 SWE-bench 提交借助激进的 scaffold 从 69% 提升到 81%。
3. 工具接口设计：Anthropic 的经验是在工具接口上花的精力应多于提示词。具体做法包括强制绝对路径；`str_replace` 只在恰好匹配一次时执行，否则报错让模型重试（这是其测试过的最可靠的编辑方式）；工具描述写清转义、无网络、后台执行、输出截断、按行范围查看等细节；用测试发现模型误解后改写工具描述。
4. 验证闭环：先写复现脚本，修复后重跑，再检查边界情况。看不到隐藏测试时，模型容易误以为已经成功。
5. 上下文管理：长任务可能有数百轮、超过 100k tokens。截断、压缩、子代理隔离决定后期是否失忆或抖动。
6. 编辑格式良好率：格式出错的编辑等于浪费一轮，弱模型上尤其明显，所以 Aider 单独报告这一指标。

## 4. 弱模型或用户可配置模型下的设计要点

### 4.1 工具调用协议兼容层
- 能力探测：配置模型时做一次探针调用，记录是否支持原生 tools、并行调用、流式参数、图片输入，以及 JSON 参数是否稳定，存入模型 profile。
- 三级回退：原生 function calling → 文本协议（XML 标签或固定围栏内的 JSON，提示词附格式说明和示例）→ CodeAct（模型直接输出要执行的 Python/bash 代码块，由 harness 执行）。OpenHands 借 LiteLLM 覆盖无函数调用模型，CodeAct 也说明"代码即动作"对弱模型更自然。
- 宽容解析：去除 markdown 围栏，修复尾逗号、单引号、未闭合括号；做类型强转（如 `"true"` 转布尔值、数字字符串转 int）；接受参数名别名（`path`/`file_path`/`filename`）；忽略多余字段。
- 解析失败作为 observation 返回模型重试，并计数。同类失败连续 N 次时自动降级协议，或改用更简单的工具（如整文件写入）。
- 默认串行：弱模型的并行工具调用容易互相冲突，并行只对 profile 中标记可靠的模型开启。
- 兼容端点差异：部分 OpenAI 兼容端点在 `tool_choice`、流式 `tool_calls` 增量、`finish_reason` 上实现不一致，需要逐个适配。Codex 仅支持 Responses API 的说法（未核实）说明，大厂工具未必兼容通用端点，这对 codeless 是机会。

### 4.2 工具 schema 设计
- 少而正交：读、写、编辑、搜索、shell、后台进程、预览状态、询问用户，约 8 个工具足够。工具越多，弱模型越难选对。
- 参数扁平、以字符串为主，避免嵌套对象和 `oneOf`，用 `enum` 收敛取值，必填项尽量少。
- 防呆：统一使用绝对路径或"相对工作区根"路径，harness 侧做规范化和越界检查（Anthropic 的 poka-yoke 经验）。
- 错误信息可操作：说明失败原因和修正方法，例如"old_string 出现 3 次，请扩大上下文或设置 replace_all"或"文件未读取，请先 read"。
- 工具描述写边界：超时、是否联网、输出截断规则、分页方式、后台进程如何查看日志。

### 4.3 编辑格式
- 默认采用 str_replace 唯一匹配，可靠且省 token。新文件或小文件（例如少于 200 行）允许整文件写入。
- 多级匹配回退：精确匹配 → 忽略行尾空白 → 忽略缩进差异 → 高阈值相似度匹配。回退命中时把实际替换的片段回显给模型。
- 按模型选择格式：参考 Aider，按模型 profile 选 str_replace、diff 或 whole，并持续统计每个模型的编辑成功率，自动切换。
- 拦截偷懒占位：检测 `... existing code ...`、`// rest unchanged` 一类占位符，拒绝写入并提示补全。
- 编辑后立即做语法检查（`python -m py_compile`、`node --check`、`tsc --noEmit` 等），结果随编辑结果一并返回，不让语法错误拖到运行时才暴露。
- 截断检测：`finish_reason=length` 时不要写入半个文件，应引导模型分块写入或改为多次 str_replace。
- Architect/Editor 拆分：强模型规划，便宜或弱模型编辑（Aider architect、Cline Plan/Act 分设模型）。codeless 可以让用户分别配置规划模型和编辑模型。

### 4.4 由 harness 驱动的结构化验证
- 不要依赖模型自己想起来验证。每轮编辑后由 harness 自动跑验证管线：语法/类型检查 → 依赖安装结果 → 进程是否存活 → 端口是否监听 → HTTP 健康检查 → 无头浏览器加载页面，收集 console error、未捕获异常和失败请求 → 可选截图（仅对支持视觉的模型发送）。
- 输出结构化，例如 `{stage, status, error_type, file, line, excerpt}`，只保留关键几十行和堆栈顶部，不回灌整段日志。
- 区分 error 和 warning，避免弱模型对告警过度反应、反复改动无关代码。
- 自修复预算：同一错误签名最多尝试 N 次；超出后停止，并向用户报告假设、已尝试的方案和建议。Replit 的反思循环和 Lovable 的 auto-fix 都是这一形态，而预算控制决定会不会出现 Bolt 用户反馈的那种高成本错误循环。
- 验收：从用户需求自动生成几条可执行的冒烟检查（访问哪个路由、页面应出现什么文本），作为"完成"的判定条件。

### 4.5 上下文管理（弱模型的窗口往往更小）
- 截断阈值按模型窗口等比缩放：超长输出落盘，回传头尾和路径，让模型按需分页读取。
- 分层压缩：先清理旧工具输出（成本最低），再做摘要。摘要必须保留用户需求、已做决策、未解决错误、文件清单和运行方式。Anthropic 也提醒，压缩过度会丢失细微但关键的上下文。
- 项目记忆：在项目根生成并维护 AGENTS.md（多家工具通用），记录技术栈、启动命令、端口、依赖和已知问题，每次会话注入。
- 状态外置：当前计划和 todo、预览运行状态、最近一次验证结果，作为结构化块在每轮注入，不依赖模型从对话历史中回忆。
- 仓库地图：小模型更依赖文件树加主要符号的摘要（Aider repo map 思路，实现细节本轮未复核）。
- 子代理：探索性读取（如"找出所有路由"）交给独立上下文，只回传结论。

### 4.6 提示词与模型适配层
- 核心提示词与模型无关：不依赖某家特有的标签习惯、thinking 参数或缓存语法，供应商差异放进 adapter。
- 每个模型一份 profile：上下文窗口、最大输出、原生工具支持、并行调用、编辑格式、温度、图片支持、推理强度参数、已知怪癖及对应的补丁提示。Cursor 官方即按模型调优指令和工具。
- 给弱模型 1 到 2 个完整的工具调用示例，至少包含一次"失败后修正"的示例。
- 系统提示词保持简短，把"如何验证、何时停止、何时询问用户"写成明确规则，不要依赖模型自行判断。
- 预置推荐模型和档位，并对未测试的模型给出能力警告。商业 App Builder 不开放模型选择正是为了守住质量下限，codeless 开放模型就需要用 profile 和评测来守。

### 4.7 循环控制与安全
- 卡死检测：同一工具同一参数连续出现、同一错误反复出现、连续数轮无文件变化时，注入"换思路"提示或请求用户输入。OpenHands 有类似机制（本轮未复核）。
- 硬预算：最大轮数、token 或费用上限、单命令超时、会话总时长，在 UI 中实时显示消耗。
- 长进程：dev server 必须后台运行，前台超时的命令转后台而不是直接杀掉（Claude Code 的做法）。
- 安全：网页内容和依赖文档可能带提示注入。沙箱默认断网或使用包管理源白名单（参考 Copilot 防火墙），密钥不进入沙箱或经代理注入，并限制容器的 CPU、内存和磁盘。

## 5. 对 codeless 的对标建议

### 5.1 评测方法
- 任务集（建议 30 到 50 个，覆盖 Python 与 Node.js）：
  - A 从零生成：FastAPI/Flask CRUD、Streamlit 看板、Express + 静态前端、Vite + React 单页。
  - B 迭代修改：在已有项目上加功能、改 UI、换数据源。
  - C 故障修复：注入运行时异常、依赖版本冲突、端口占用、前端 console 报错。
  - D 长会话：10 轮以上连续指令，考察上下文保持。
- 对照组：在同一 Docker 基础镜像中运行 Claude Code（`claude -p` headless）和 Codex（`codex exec`）。为了把 harness 差异与模型差异分开：
  - codeless 配 Claude 模型，对比 Claude Code；
  - codeless 配 GPT 模型，对比 Codex；
  - codeless 配若干开源或较弱模型，测性能衰减曲线（这是 codeless 的主场）。
- 指标：自动验收通过率（冒烟脚本加人工抽检）、首次成功率、自修复成功率、平均轮数和工具调用数、token 与费用、墙钟时间、编辑失败率、工具调用解析失败率、用户打断次数。

### 5.2 能力差距优先级（与第 2 节一致）
- P0：精确编辑加多级回退、工具调用兼容层、后台进程加日志回灌、harness 强制验证管线、运行时错误自修复（带预算）、检查点回滚、上下文截断与压缩、AGENTS.md、会话持久化、模型 profile、排队消息与打断。
- P1：Plan 模式和 todo、无头浏览器验证与截图、权限分级、网络白名单、MCP、headless API、成本统计、图片输入、规划与编辑分设模型。
- P2：子代理并行、skills/hooks、自动记忆、云端异步执行、工具按需加载。

## 参考来源

官方文档与一手资料：
- https://code.claude.com/docs/en/how-claude-code-works
- https://code.claude.com/docs/en/tools-reference
- https://code.claude.com/docs/en/sub-agents
- https://learn.chatgpt.com/docs/codex/cli （由 developers.openai.com/codex/cli 重定向）
- https://learn.chatgpt.com/docs/config-file/config-advanced.md （由 developers.openai.com/codex/config-advanced.md 重定向）
- https://cursor.com/docs/agent/overview
- https://cursor.com/help/ai-features/background-agents
- https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/customize-the-agent-firewall
- https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/customize-the-agent-environment
- https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/checkpointing.md
- https://aider.chat/docs/more/edit-formats.html
- https://docs.cline.bot/exploring-clines-tools/checkpoints
- https://ampcode.com/manual
- https://lovable.dev/en/blog/agent-mode-beta
- https://docs.replit.com/replitai/app-testing
- https://docs.replit.com/updates/2025/09/12/changelog
- https://docs.replit.com/ko/updates/2026/03/13/changelog.md
- https://www.npmjs.com/package/bolt.diy
- https://www.tencentcloud.com/document/product/1256/77266
- https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/
- https://www.snorkel.ai/blog/terminal-bench-2-0/
- https://proceedings.iclr.cc/paper_files/paper/2026/hash/444a3737adaee10d86ad2ef5f74468e6-Abstract-Conference.html
- https://www.anthropic.com/engineering/swe-bench-sonnet
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents （经二手摘要引用）
- https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools

二手资料（可信度较低，相关结论已在文中标注）：
- https://tutorialsdojo.com/openai-codex-cheat-sheet/
- https://codex.danielvaughan.com/2026/04/23/codex-cli-custom-model-providers-configuration-guide/
- https://www.morphllm.com/bolt-vibe-coding
- https://0jo2xuhypsg.v0.build/docs/sandbox （v0 文档镜像，来源存疑）
- https://www.createwith.com/tool/vercel/updates/vercel-opens-app-building-with-the-new-v0-api
- https://www.createwith.com/tool/vercel/updates/vercel-opens-vercel-sandbox-to-cursor-cloud-agents
- https://bhakthan.substack.com/p/the-openhands-software-agent-sdk
- https://runtimewire.com/article/cognition-devin-cloud-terminal-ssh
- https://runtimewire.com/article/devin-multi-vm-cloud-agents-modal-outposts
- https://www.zenml.io/llmops-database/building-and-deploying-production-cloud-agents-for-software-engineering
- https://alpha.us-east-2.kiro.adc.aws.dev/blog/introducing-kiro/ （Kiro 官方博客镜像）
- https://medium.com/@arnab.kg.co.in/mastering-kiro-the-definitive-guide-to-spec-driven-ai-development-b14e82ff4ea5
- https://juejin.cn/post/7547498416759652404
- https://www.datacamp.com/tutorial/trae-ai
- https://we0.ai/ru/articles/trae-work-ai-office-platform-analysis
- https://gitcode.csdn.net/69f64cfa54b52172bc717635.html
- https://www.ai-indeed.com/encyclopedia/28679.html
- https://www.codesota.com/benchmark/terminal-bench
- https://www.morphllm.com/terminal-bench-2
- https://www.morphllm.com/swe-benchmark
- https://aiweekly.co/alerts/openai-drops-swe-bench-verified-backs-pro-after-flaw-audit
- https://leaderboard.steel.dev/leaderboards/aider/
