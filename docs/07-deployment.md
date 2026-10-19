# 部署与使用指南

把 codeless 在一台机器上跑起来，接上你自己的大模型，然后用一句话做出能运行、能预览的程序。

> 这是 v1 的说明。v0 POC（Gitea + CI 发布链路）的启动方式见 [poc/DEPLOYMENT.md](../poc/DEPLOYMENT.md)。

## 1. 前置条件

| 要求 | 说明 |
|---|---|
| Docker | Docker Desktop 或 Linux 上的 Docker Engine，必须在运行。agent 的命令都在沙箱容器里执行 |
| 磁盘 | 约 3 GB：沙箱镜像约 1.1 GB，编排服务镜像约 1.5 GB（不带浏览器自检约 600 MB） |
| 内存 | 每个活跃项目的沙箱默认上限 2 GB，闲置 30 分钟自动停掉 |
| 模型 | 任意支持工具调用（function calling）的模型：OpenAI 兼容协议或 Anthropic 协议 |
| 网络 | 宿主机要能访问模型 API；沙箱只能访问包管理源（npm、PyPI 及国内镜像） |

## 2. 用 Docker Compose 运行（推荐）

```sh
git clone https://github.com/decli/codeless.git
cd codeless
make sandbox-image          # 构建任务沙箱镜像：Python 3.12 + Node.js 22 + 常用依赖
docker compose up -d --build
```

打开 <http://localhost:8000>，在「设置 → 模型」里添加一个模型，回到首页写一句需求就能开始。

国内网络构建慢，可以换源：

```sh
docker build -f docker/sandbox.Dockerfile -t codeless-sandbox:latest \
  --build-arg APT_MIRROR=mirrors.aliyun.com \
  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com .
```

编排服务镜像同理（`docker/app.Dockerfile` 支持 `PIP_INDEX_URL`、`NPM_REGISTRY`，不需要浏览器自检时加 `WITH_BROWSER=0`）。

## 3. 本机直接运行（开发调试）

需要 Python 3.11+、Node.js 20+、Docker。

```sh
make install                # 建 .venv，装后端和前端依赖，装无头浏览器
make sandbox-image
make run                    # 构建前端并启动 http://localhost:8000
```

改前端时用 `make dev` 起后端，`make web-dev` 起 Vite 开发服务器（<http://localhost:5173>，接口转发到 8000）。

没有 Docker 时可以用本地进程模式：`.venv/bin/python -m codeless --local`。这时 agent 的命令直接在本机执行，**没有任何隔离**，只适合在自己信任的环境里调试。

## 4. 配置模型

在「设置 → 模型」里添加，可以添加多个，每个项目单独选，也可以在输入框旁边随时切换。内置了这些服务的预设，选中后只需要填 API Key：

| 预设 | 协议 | 说明 |
|---|---|---|
| DeepSeek | OpenAI 兼容 | 思考模式在工具调用循环里会自动回传 `reasoning_content` |
| 通义千问、Kimi、智谱 GLM、豆包、硅基流动 | OpenAI 兼容 | 豆包的模型名填推理接入点 ID |
| Claude | Anthropic | 支持自适应思考、提示缓存、看图 |
| OpenAI、OpenRouter | OpenAI 兼容 | |
| Ollama | OpenAI 兼容 | 本地模型，需要支持工具调用；不在 Docker 里运行时地址改成 `http://localhost:11434/v1` |
| 其他 | OpenAI 兼容 | 任何兼容 `/chat/completions` 的服务 |

几个选项的含义：

- **支持识图**：打开后，用户贴的截图、自检时的预览截图会发给模型。模型不支持看图时不要打开，否则请求会报错。
- **思考**：Anthropic 协议下开启思考；OpenAI 兼容协议由模型自己决定。
- **上下文窗口**：超过约 80% 时把历史压缩成摘要。填小了浪费，填大了会超长报错，以服务商文档为准。
- **价格**：填了就按用量计费并显示花费，可以在「设置 → Agent」里给单轮设花费上限。

API Key 只保存在数据目录的 `settings.json` 里，接口不会返回它，也不会进入沙箱。

也可以用环境变量预置一个默认模型（只在第一次启动、还没有任何模型时生效），见 `.env.example` 里的 `LLM_*`。

## 5. 环境变量

复制 `.env.example` 为 `.env` 修改。常用的：

| 变量 | 默认 | 作用 |
|---|---|---|
| `CODELESS_PORT` | `8000` | 端口 |
| `CODELESS_TRUSTED_HOSTS` | 空 | 接口只接受 Host 为 localhost / 127.0.0.1 的请求（防 DNS 重绑定）。从局域网或域名访问时把地址加进来 |
| `SANDBOX_BACKEND` | `docker` | `docker` 隔离运行；`local` 直接在本机运行 |
| `SANDBOX_NETWORK` | `allowlist` | `allowlist` 只能访问包管理源；`open` 不限制；`none` 完全断网 |
| `SANDBOX_ALLOWED_HOSTS` | 空 | 白名单之外再放行的域名，逗号分隔 |
| `SANDBOX_CPUS` / `SANDBOX_MEMORY` | `2` / `2g` | 每个沙箱的资源上限 |
| `SANDBOX_IDLE_MINUTES` | `30` | 沙箱闲置多久后停掉（文件保留，下次打开自动拉起） |
| `NPM_REGISTRY` / `PIP_INDEX_URL` | 空 | 沙箱里用的镜像源，会自动加进白名单 |
| `UPSTREAM_PROXY` | 空 | 白名单代理经企业出网代理访问外网 |
| `SANDBOX_CA_BUNDLE` | 空 | 企业 HTTPS 中间人证书，挂进沙箱 |
| `PREVIEW_DOMAIN` | `localhost` | 预览子域名的后缀 |
| `BROWSER_CHECK` | `auto` | 装了 Playwright 就用无头浏览器自检；`off` 只做 HTTP 检查 |

## 6. 预览是怎么访问的

每个项目的预览在自己的子域名下：`http://<项目 id>.localhost:8000/`。浏览器会把 `*.localhost` 解析到本机，不需要改 hosts。用子域名而不是子路径，是为了让站点跑在根目录，绝对路径的资源不会失效。

agent 起的其他端口也能访问：`http://<项目 id>-<端口>.localhost:8000/`。

要在局域网里访问，需要一个能泛解析到这台机器的域名，比如 `PREVIEW_DOMAIN=codeless.lan` 并让 `*.codeless.lan` 指向本机，同时设置 `CODELESS_TRUSTED_HOSTS`。

## 7. 数据目录

默认在 `./data`：

```
data/
  settings.json            模型配置（含 API Key）和 agent 设置
  projects/<id>/
    meta.json              项目名、模型、预览命令、累计用量
    workspace/             项目代码，git 仓库，每轮一个检查点
    messages.json          发给模型的对话历史
    timeline.json          界面上的时间线
    uploads/ shots/        用户贴的图、自检截图
```

备份就是拷贝这个目录。项目代码可以在界面上打包下载，也可以直接到 `workspace/` 里用 git 查看。

## 8. 排查

| 现象 | 处理 |
|---|---|
| 首页提示「沙箱不可用」 | Docker 没运行，或者沙箱镜像没构建（`make sandbox-image`） |
| `npm install` / `pip install` 卡住或 403 | 域名不在白名单。看终端标签页里的日志，把需要的域名加到 `SANDBOX_ALLOWED_HOSTS` |
| 预览一直显示「还没有运行」 | 看终端标签页的预览进程日志；服务要监听 `0.0.0.0` 和 `$PORT` |
| 模型报错 400 | 在设置里点「测试连接」。常见原因：模型不支持工具调用、不支持看图却打开了识图、上下文窗口填得比实际大 |
| 接口返回「不允许的 Host」 | 从非 localhost 地址访问，设置 `CODELESS_TRUSTED_HOSTS` |

查看编排服务日志：`docker compose logs -f codeless`。

## 9. 清理

```sh
docker compose down
docker rm -f $(docker ps -aq --filter name=codeless-p-) codeless-egress
docker network rm codeless-sandbox
```

删除项目在界面上操作，会同时删除它的沙箱容器和文件。

## 10. 安全须知

- 编排服务通过 `docker.sock` 管理沙箱，等于拥有宿主机的 root 权限。只在自己的机器或受信任的服务器上部署，不要把 8000 端口直接暴露到公网。
- 沙箱是普通容器：内部网络、只能经白名单代理出网、限制 CPU 和内存、看不到 API Key 和数据目录。它适合自用和小团队，**不是多租户级别的隔离**；对外提供服务需要换成 gVisor 或 microVM，见[技术方案](03-architecture.md)。
- 预览页面和管理界面不同源，预览里的脚本调不了管理接口（写操作检查 Origin，所有接口检查 Host）。
- 本地进程模式（`--local`）没有任何隔离，agent 的命令以你的身份在本机执行。
