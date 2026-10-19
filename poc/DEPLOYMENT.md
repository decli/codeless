# 接入与启动指南

把 codeless 在一台机器上跑起来，接上你自己的大模型，然后用一句话做出一个能访问的网站。

全程在本机容器里，不依赖任何云服务（除了模型 API）。

## 1. 前置条件

| 要求 | 说明 |
|---|---|
| Docker Desktop | 必须在运行状态。Linux 上用 Docker Engine 也可以 |
| 磁盘 | 约 2 GB（沙箱镜像 400 MB、Gitea 和 runner 各几百 MB） |
| 内存 | 4 GB 以上空闲即可，全部服务加起来约 1 GB |
| 端口 | 8000、8081、3000 三个端口不被占用 |
| 模型 API key | 默认用 DeepSeek，也可以换成任何 OpenAI 兼容服务，见第 5 节 |
| 网络 | 宿主机要能访问模型 API 和 Docker Hub。**任务沙箱本身是断网的** |

Python 和 Node 不用装，都在容器里。

## 2. 五分钟跑起来

```sh
git clone https://github.com/decli/codeless.git
cd codeless/poc
cp .env.example .env
```

编辑 `.env`，填入你的模型 key：

```
LLM_API_KEY=sk-你的key
```

然后一条命令拉起全部服务：

```sh
sh infra/setup.sh
```

首次执行大约两三分钟（要拉镜像、建管理员、注册 runner）。看到下面这样的输出就成了：

```
完成：
  Web 界面   http://localhost:8000
  站点入口   http://localhost:8081
  Gitea      http://localhost:3000   codeless / codeless-poc-2026
```

打开 <http://localhost:8000>，写一句需求，点发送。

## 3. setup.sh 做了什么

脚本是幂等的，重复执行不会破坏已有数据。它按顺序做这些事：

1. 构建任务沙箱镜像 `codeless-sandbox-py`（Python 3.12 + pytest + ruff）。
2. 启动 Gitea，等它就绪。
3. 创建管理员账号（已存在就跳过），生成两个凭证：给编排服务用的 API token、给 runner 用的注册 token。
4. 把凭证写进 `infra/.env`（compose 用）和 `poc/.env`（命令行用）。这两个文件都在 `.gitignore` 里。
5. 启动 Actions runner、站点服务器 nginx、Web 界面。

想分步执行或者排查问题，可以直接用 compose：

```sh
docker compose -f infra/docker-compose.yml ps
```

```sh
docker compose -f infra/docker-compose.yml logs -f web
```

## 4. 服务和地址

| 地址 | 服务 | 作用 |
|---|---|---|
| <http://localhost:8000> | `codeless-web` | Web 界面加编排服务，通过宿主机 docker socket 起任务沙箱 |
| `http://<项目名>.localhost:8081/` | `codeless-pages` | 做好的站点，每个项目一个子域名 |
| <http://localhost:8081> | 同上 | 所有已部署站点的列表 |
| <http://localhost:3000> | `codeless-gitea` | 代码仓库和 CI 记录，看代码不用登录 |
| 无端口 | `codeless-runner` | 跑 Gitea Actions，复用本地沙箱镜像 |
| 无端口 | `codeless-task-*` | 任务沙箱，每个任务一个，断网，用完即毁 |

站点用子域名而不是子目录，是因为验收测试里站点跑在服务器根目录。放进子目录会让站点里的绝对路径链接全部 404，而测试却是绿的——两边环境必须一致。`*.localhost` 现代浏览器都会解析到 127.0.0.1，不用改 hosts。

## 5. 接入模型

### 5.1 默认：DeepSeek

`.env` 里三行就是全部接入点：

```
LLM_API_KEY=sk-你的key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
```

选 `deepseek-flash` 是因为便宜：输入 $0.15–0.30 / 百万 token（命中缓存 $0.003–0.006），输出 $0.60–1.20，做一个静态站点单次一到两分钱。

（`DEEPSEEK_API_KEY` 这类旧名字仍然兼容，两套都配时以 `LLM_*` 为准。）

### 5.2 换成别的模型或供应商

改这三个值即可，任何 OpenAI 兼容的服务都能接：

```
LLM_API_KEY=你的key
LLM_BASE_URL=供应商的 base_url
LLM_MODEL=模型名
```

举例：

| 供应商 | BASE_URL | 说明 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | 默认 |
| 阿里云百炼 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 填 Qwen 系列模型名 |
| 月之暗面 | `https://api.moonshot.cn/v1` | |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | |
| 本地 Ollama | `http://host.docker.internal:11434/v1` | 容器里要用 `host.docker.internal` 才能访问宿主机 |
| OpenAI | `https://api.openai.com/v1` | |

**对模型的硬性要求**：必须支持 function calling（工具调用）和流式输出。不支持工具调用的模型跑不了，编码 agent 全靠工具干活。

模型的思考内容（`reasoning_content`）有就显示，没有也不影响运行。

改完 `.env` 后重启 Web 服务：

```sh
docker compose -f infra/docker-compose.yml up -d --force-recreate web
```

### 5.3 调整上限

```
MAX_USD=0.50     # 单个任务最多花多少钱，超了就停下来报阻塞
MAX_TURNS=40     # 每一轮修复最多多少次模型调用
```

还有几个在 `agent/config.py` 里：修复轮数 3、单条命令超时 120 秒、沙箱 2 CPU / 2G 内存。

## 6. 怎么用

1. 打开 <http://localhost:8000>，在输入框写需求，比如"帮我做一个简单的个人博客，首页按时间列出文章，每篇文章有独立页面"。
2. 右边会实时显示：生成验收测试 → 起沙箱 → 模型的思考和输出 → 写文件 → 跑 lint、单测、验收测试 → 推 Gitea → CI 部署。
3. 完成后出现"打开站点"链接，点开就是做好的网站。
4. **继续在输入框里说话就能改**：「换成卡片风格」「加一个联系页」「首页标题改成 XX」。同一个项目、同一个仓库、同一个站点地址，新要求会追加新的验收测试，老测试继续跑，改坏了老功能会被当场拦住。
5. 顶栏的「代码」「CI」可以看生成的源码和流水线记录。

### 命令行方式

界面之外也能用 CLI（需要先 `make setup` 建虚拟环境）：

```sh
.venv/bin/python -m agent.cli "帮我做一个咖啡店官网" --push
```

```sh
.venv/bin/python -m agent.cli "做一个统计词频的工具" --template python-cli --push
```

## 7. 常见问题

**Docker 守护进程没在运行**
启动 Docker Desktop 再执行 `sh infra/setup.sh`。

**端口被占用**
`docker compose up` 会报 `port is already allocated`。先查是谁占着：

```sh
lsof -nP -iTCP:8081 -sTCP:LISTEN
```

改端口就编辑 `infra/docker-compose.yml` 的 `ports`，同时把 `web` 服务的 `PAGES_URL` 改成同一个端口（两处必须一致，否则界面给出的链接打不开）。

**CI 一直不跑**
看 runner 有没有注册上：

```sh
docker compose -f infra/docker-compose.yml logs runner | tail -20
```

出现 `Runner registered successfully` 才算好。没有就重新执行 `sh infra/setup.sh`。

**任务一直"进行中"**
看编排服务日志：

```sh
docker compose -f infra/docker-compose.yml logs -f web
```

一个静态站点任务正常一到三分钟。超过五分钟多半是模型 API 慢或者超时，任务有花费和轮数上限，最终会自己停下来并给出阻塞原因。

**站点打不开**
先确认项目状态是"已上线"。再直接访问 <http://localhost:8081>，看目录列表里有没有这个项目名。都正常就是浏览器没解析 `*.localhost`，用 `curl -I http://项目名.localhost:8081/` 验证一下。

**模型报 400 / 不支持工具调用**
换一个支持 function calling 的模型。

## 8. 停止与清理

停掉全部服务（数据保留）：

```sh
docker compose -f infra/docker-compose.yml down
```

删掉某个项目：删掉 `poc/workspaces/<项目名>/` 目录，界面上就不再显示（项目状态就存在这个目录里）。

彻底清空（包括 Gitea 仓库、已部署站点、全部项目）：

```sh
docker compose -f infra/docker-compose.yml down -v && rm -rf workspaces/* infra/gitea-data infra/runner-data
```

## 9. 安全须知

这套东西是本地验证用的，**不要直接暴露到公网**。

**密钥**

- 模型 key 只存在 `poc/.env` 和 `infra/.env`，两个文件都在 `.gitignore` 里，仓库里只有 `.env.example` 占位符。
- key 不会进入任务沙箱，也不会进入模型上下文。沙箱 `--network none` 完全断网，agent 执行的命令拿不到任何凭证。
- Git 推送用的 token 只出现在一次性的推送 URL 里，不写进仓库的 remote 配置。
- 提交前可以自查一遍：

```sh
git diff --cached | grep -nE "sk-[a-zA-Z0-9]{20,}" && echo "发现疑似密钥，别提交"
```

想自动化就装个 pre-commit 钩子：

```sh
printf '#!/bin/sh\ngit diff --cached | grep -qE "sk-[a-zA-Z0-9]{20,}" && { echo "拒绝提交：暂存区里有疑似密钥"; exit 1; }\nexit 0\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

**其他风险**

- 编排服务挂载了宿主机的 `/var/run/docker.sock`，等于拥有宿主机 root 权限。这是本地验证的妥协，真上多租户必须换成 microVM 级隔离，见[技术方案](03-architecture.md)第 5.5 节。
- Gitea 的默认管理员密码 `codeless-poc-2026` 是明文写在脚本里的。只在本机用没关系，要改就在执行前设环境变量：`GITEA_PASSWORD=你的密码 sh infra/setup.sh`。
- 生成的站点由 nginx 直接托管，没有鉴权，同一局域网内可以访问到。

## 10. 目录速查

```
poc/
├── agent/          编排与 agent 循环（模型调用、沙箱、工具、任务、发布）
├── web/            Web 界面（FastAPI + 单页 HTML）
├── templates/      黄金路径模板：static-site、python-cli
├── docker/         沙箱镜像和 Web 服务镜像
├── infra/          docker-compose、nginx、runner 配置、一键脚本
├── workspaces/     每个项目一个目录（代码、git 历史、项目状态），已忽略
└── .env            密钥和配置，已忽略
```

加一条新的黄金路径 = 加一个模板目录，再在 `agent/templates.py` 里加一条配置，编排逻辑不用动。
