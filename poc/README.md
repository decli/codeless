# codeless POC

> 这是 v0 POC，已归档。当前版本 v1 在仓库根目录，见[根目录 README](../README.md)。

浏览器里写一句需求 → 大模型在容器里写代码、跑测试 → CI 构建部署 → 点开链接就是做好的网站。

全程跑在本机容器里，用户不需要打开 IDE，也不用碰命令行。

完整的接入与启动说明（前置条件、换模型、排查、清理、安全）见[POC 接入与启动指南](DEPLOYMENT.md)，下面是最短路径。

## 启动

需要 Docker Desktop 和一个模型 API key（默认 DeepSeek，也可以换成任何 OpenAI 兼容服务）。

```sh
cp .env.example .env    # 填入 LLM_API_KEY
sh infra/setup.sh       # 拉起全部服务，大约两三分钟
```

然后打开 <http://localhost:8000>，写下需求，点开始。

| 地址 | 是什么 |
|---|---|
| <http://localhost:8000> | Web 界面：提需求、看进度、继续迭代 |
| `http://<项目名>.localhost:8081/` | 做好的站点，每个项目一个子域名 |
| <http://localhost:8081> | 所有已部署站点的列表 |
| <http://localhost:3000> | Gitea，看代码和 CI 记录，账号 `codeless` / `codeless-poc-2026`（只读不用登录） |

站点用子域名而不是子目录，是因为验收测试里站点跑在服务器根目录；放进子目录会让绝对路径链接全部 404，测试却是绿的。让两边环境一致才不会漏。

## 组成

五个部分，都在容器里：

| 服务 | 镜像 | 作用 |
|---|---|---|
| `web` | 自建 | Web 界面 + 编排服务，通过宿主机 docker socket 起沙箱 |
| 沙箱 | `codeless-sandbox-py` | 每个任务一个，断网，agent 在里面写码跑测试，用完即毁 |
| `gitea` | `gitea/gitea` | 代码托管，触发 CI |
| `runner` | `gitea/act_runner` | 跑 Gitea Actions，复用本地沙箱镜像，不拉外网镜像 |
| `pages` | `nginx:alpine` | 托管 CI 部署出来的站点 |

CI 和 nginx 共用一个 docker 卷 `codeless_sites`，CI 把 `dist/` 拷进去，nginx 立刻就能访问到。

## 一次任务发生了什么

1. 用模板初始化工作区，`git init` 并提交。
2. 测试 agent 把需求翻成 pytest 验收测试，写进 `tests/acceptance/` 并锁定（记下 sha256）。
3. 起一个断网容器，工作区挂进去。
4. 编码 agent 用四个工具干活：`list_files`、`read_file`、`write_file`、`run`，自己跑 lint、单测、验收测试。
5. 每轮结束后编排侧独立做三件事：还原被改动的锁定文件、删掉新增的配置文件、自己再跑一遍三项检查。
6. 全绿就提交，不绿就把失败输出回灌给 agent，最多修 3 轮。
7. 推到 Gitea，Actions 里再独立跑一遍检查，构建，把 `dist/` 部署到站点卷。
8. 界面上出现"打开站点"的链接。

继续提要求时（第二句话开始）走的是迭代模式：同一个工作区、同一个仓库、同一个站点地址，只针对新要求生成新的验收测试追加进去，**历史验收测试继续跑**，所以改新功能改坏了老功能会被当场拦住。

验收测试怎么测一个网站：夹具先 `./run.sh build`，再用 `http.server` 起一个本地服务器，测试通过 HTTP 访问页面、检查内容和站内链接是否可达。跟真实访问的路径一致。

## 两条黄金路径

| 模板 | 产物 | 发布方式 |
|---|---|---|
| `static-site` | 纯静态网站（HTML/CSS/JS） | 部署到 nginx，可直接访问 |
| `python-cli` | Python 命令行工具 | 打包成 `.pyz` 发到 Gitea Release |

加一条新路径 = 加一个模板目录 + 在 `agent/templates.py` 里加一条配置，编排逻辑不用动。

## 命令行用法

界面之外也可以直接用 CLI（需要先 `make setup` 建虚拟环境）：

```sh
.venv/bin/python -m agent.cli "帮我做一个咖啡店官网" --push
```

```sh
.venv/bin/python -m agent.cli "做一个统计词频的工具" --template python-cli --push
```

常用参数：`--name` 指定项目名，`--max-usd` 限制花费，`--keep-sandbox` 保留容器排查，`-v` 打印完整输出。

## 边界与上限

任何一层循环都有上限，超了就停下来报阻塞原因，不会无限重试烧钱。

| 项 | 默认值 | 改哪里 |
|---|---|---|
| 单任务花费 | $0.50 | `.env` 的 `MAX_USD` |
| 对话轮数 | 40 | `.env` 的 `MAX_TURNS` |
| 修复轮数 | 3 | `agent/config.py` |
| 单条命令超时 | 120 秒 | `agent/config.py` |
| 沙箱资源 | 2 CPU、2G 内存、无网络 | `agent/sandbox.py` |

## 安全边界

- 沙箱 `--network none`，agent 执行的命令碰不到网络，也拿不到任何凭证。
- git 推送和 Gitea API 都在编排侧执行，token 只出现在一次性的推送 URL 里，不写进仓库配置。
- 锁定路径（`tests/acceptance/`、`run.sh`、`pyproject.toml`、`.gitea/`）被改会自动还原；新增的 `ruff.toml`、`conftest.py` 这类能覆盖配置的文件会被删除。
- CI 只允许挂载站点卷一个卷（`runner.yaml` 的 `valid_volumes`）。
- 编排服务能访问宿主机的 docker socket，等于有宿主机 root 权限。这是本地验证的妥协，真上多租户必须换成 microVM 级隔离，见[技术方案](../docs/03-architecture.md)第 5.5 节。

## 已知问题

- 验收测试由模型生成，可能本身写错或写得过严，这时 agent 会一直修不过去——这正是要观察的失败模式，`.codeless/task.json` 里有记录。
- 沙箱断网意味着站点不能用 CDN 上的字体、框架、图片，只能自己写样式。要支持第三方依赖，得给沙箱配带白名单的包管理代理。
- 没有需求澄清环节，需求直接进了测试生成。模糊需求会直接变成错误的验收标准。
- 迭代时新验收测试和老验收测试可能互相矛盾（比如改了页面标题），这时会一直修不过去，只能新建项目。
- 项目状态存在各自工作区的 `.codeless/project.json` 里，服务重启会重新加载；删掉工作区项目就没了。
- CI 检出用的是默认分支最新提交，不是精确 commit；单人本地够用。
