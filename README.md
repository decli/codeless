# codeless

对话式编程 agent：用一句话描述想要的程序，codeless 在隔离沙箱里写代码、运行、实时预览，出了问题自己修，你随时插话改需求。支持 Python 和 Node.js，基座模型自己配置：DeepSeek、通义千问、Kimi、智谱、Claude、OpenAI、本地 Ollama 都能接。

## 能做什么

- **写任意程序**：网页应用、小游戏、数据看板、Web 接口、命令行工具、数据处理脚本。内置 React + Vite、Express、FastAPI 起步模板。
- **实时预览**：开发服务器跑在沙箱里，界面右侧直接看效果，热更新即时生效，可以切换手机宽度。
- **自己发现并修复 bug**：每轮结束后系统检查进程、端口、页面资源和浏览器控制台，有问题自动交回 agent 修；你在预览里遇到的报错也能一键交给它。
- **随时改**：agent 工作时可以插话补充要求，也可以立即停止；每轮结束自动提交到项目自己的 git 仓库作为检查点，可以查看改动、撤销本轮、恢复到任意版本（只在本地，不推远程，见[代码怎么保存](docs/07-deployment.md#8-代码怎么保存)）。
- **模型可配置**：OpenAI 兼容协议和 Anthropic 协议，多个模型随时切换，显示 token 用量和花费。
- **安全隔离**：每个项目一个 Docker 沙箱，只能访问包管理源，看不到 API Key 和宿主机文件。

## 快速开始

需要 Docker 和一个支持工具调用的模型 API Key。

```sh
git clone https://github.com/decli/codeless.git
cd codeless
make sandbox-image            # 构建任务沙箱镜像（Python 3.12 + Node.js 22）
docker compose up -d --build
```

打开 <http://localhost:8000>，在「设置 → 模型」里选一个服务商、填上 API Key，回到首页写下需求即可。

本机开发、换源、环境变量、排查和安全须知见[部署与使用指南](docs/07-deployment.md)。

## 开发

```sh
make install                  # 后端依赖装进 .venv，前端依赖装进 web/node_modules
make run                      # 构建前端并启动 http://localhost:8000
make test                     # 假模型 + 本地进程沙箱跑完整流程，不需要真实 API Key
make test-all                 # 加上 Docker 沙箱和无头浏览器测试
make lint
```

| 目录 | 内容 |
|---|---|
| `codeless/` | 编排服务：agent 循环、工具、模型适配、沙箱、预览代理与自检、接口 |
| `web/` | 界面：React + TypeScript + Vite |
| `sandbox/starters/` | 起步模板 |
| `docker/` | 沙箱镜像和编排服务镜像 |
| `tests/` | 单元测试和端到端测试，`fake_llm.py` 模拟两种协议的模型服务 |
| `poc/` | v0 POC（需求 → 沙箱 → Gitea CI 发布），已归档 |

## 文档

| 文档 | 内容 |
|---|---|
| [需求说明](docs/01-requirements.md) | 愿景、目标用户、用户旅程、功能与非功能需求、MVP 范围 |
| [可行性评估](docs/02-feasibility.md) | 结论、市场信号、按项目类型的可行度、主要挑战与对策、成本测算 |
| [技术方案](docs/03-architecture.md) | 总体架构、核心流程、组件选型、安全设计 |
| [路线图](docs/04-roadmap.md) | 分阶段计划、退出标准、核心指标 |
| [决策记录](docs/05-decisions.md) | 架构决策和待拍板的问题 |
| [POC 验证结果](docs/06-poc-findings.md) | v0 实测数据、暴露的问题 |
| [部署与使用指南](docs/07-deployment.md) | 前置条件、启动、配置模型、环境变量、排查、安全须知 |
| [行业 agent 调研](docs/08-agent-research.md) | Claude Code、Codex 等产品和成熟 agent 能力体系，codeless 的差距与优先级 |
| [v1 设计](docs/09-v1-design.md) | 架构、agent 循环、能力对照、关键决策 |
