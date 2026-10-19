"""运行配置：只读环境变量，启动时确定，运行中不变。

模型这类用户在界面里改的东西不放这里，放 settings.py。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 默认放行的包管理源：官方源加国内常用镜像。沙箱出网只能到这些域名。
DEFAULT_ALLOWED_HOSTS = (
    "registry.npmjs.org",
    "registry.npmmirror.com",
    "cdn.npmmirror.com",
    "registry.yarnpkg.com",
    "pypi.org",
    "files.pythonhosted.org",
    "pypi.tuna.tsinghua.edu.cn",
    "mirrors.aliyun.com",
    "mirrors.cloud.tencent.com",
    "repo.huaweicloud.com",
)


def load_dotenv(path: Path) -> None:
    """极简 .env 读取，已存在的环境变量优先，不引第三方依赖。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _host_of(url: str) -> str:
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0].lower()


@dataclass
class Config:
    data_dir: Path = ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8000

    # 沙箱
    sandbox_backend: str = "docker"  # docker | local
    sandbox_image: str = "codeless-sandbox:latest"
    sandbox_network: str = "allowlist"  # allowlist | open | none
    sandbox_cpus: str = "2"
    sandbox_memory: str = "2g"
    sandbox_idle_minutes: int = 30
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    npm_registry: str = ""
    pip_index_url: str = ""
    upstream_proxy: str = ""
    sandbox_ca_bundle: str = ""
    starters_dir: Path = ROOT / "sandbox" / "starters"

    # 预览：每个项目一个子域名 <id>.<preview_domain>
    preview_domain: str = "localhost"
    # 接口只认这些 Host，防 DNS 重绑定；部署到局域网或服务器时加上实际的域名或 IP，* 表示不检查
    trusted_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "::1")

    # 编排服务自己跑在容器里时，起沙箱要用宿主机路径；为空则启动时自动探测
    data_host_dir: Path | None = None
    in_docker: bool = False

    # 预览自检用的浏览器：auto 表示装了 playwright 就用
    browser_check: str = "auto"
    browser_path: str = ""

    extra: dict[str, str] = field(default_factory=dict)

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @classmethod
    def load(cls) -> Config:
        load_dotenv(ROOT / ".env")
        c = cls()
        if _env("CODELESS_DATA_DIR"):
            c.data_dir = Path(_env("CODELESS_DATA_DIR")).expanduser().resolve()
        c.host = _env("CODELESS_HOST", c.host)
        c.port = int(_env("CODELESS_PORT", str(c.port)))
        c.sandbox_backend = _env("SANDBOX_BACKEND", c.sandbox_backend).lower()
        c.sandbox_image = _env("SANDBOX_IMAGE", c.sandbox_image)
        c.sandbox_network = _env("SANDBOX_NETWORK", c.sandbox_network).lower()
        c.sandbox_cpus = _env("SANDBOX_CPUS", c.sandbox_cpus)
        c.sandbox_memory = _env("SANDBOX_MEMORY", c.sandbox_memory)
        c.sandbox_idle_minutes = int(_env("SANDBOX_IDLE_MINUTES", str(c.sandbox_idle_minutes)))
        c.npm_registry = _env("NPM_REGISTRY")
        c.pip_index_url = _env("PIP_INDEX_URL")
        c.upstream_proxy = _env("UPSTREAM_PROXY")
        c.sandbox_ca_bundle = _env("SANDBOX_CA_BUNDLE")
        c.preview_domain = _env("PREVIEW_DOMAIN", c.preview_domain).lower()
        extra_hosts = [h.strip().lower().strip("[]") for h in _env("CODELESS_TRUSTED_HOSTS").split(",") if h.strip()]
        if c.host not in ("0.0.0.0", "::", ""):
            extra_hosts.append(c.host.lower())
        c.trusted_hosts = tuple(dict.fromkeys([*c.trusted_hosts, *extra_hosts]))
        c.browser_check = _env("BROWSER_CHECK", c.browser_check).lower()
        c.browser_path = _env("BROWSER_PATH")
        if _env("CODELESS_STARTERS_DIR"):
            c.starters_dir = Path(_env("CODELESS_STARTERS_DIR"))
        host_dir = _env("CODELESS_DATA_HOST_DIR")
        c.data_host_dir = Path(host_dir) if host_dir else None
        c.in_docker = _env("CODELESS_IN_DOCKER") == "1" or Path("/.dockerenv").exists()

        hosts = list(DEFAULT_ALLOWED_HOSTS)
        hosts += [h.strip().lower() for h in _env("SANDBOX_ALLOWED_HOSTS").split(",") if h.strip()]
        for mirror in (c.npm_registry, c.pip_index_url):
            if mirror:
                hosts.append(_host_of(mirror))
        c.allowed_hosts = tuple(dict.fromkeys(hosts))

        c.projects_dir.mkdir(parents=True, exist_ok=True)
        return c
