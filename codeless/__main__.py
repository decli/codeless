"""启动：python -m codeless（或安装后直接 codeless）。"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="codeless", description="对话式编程 agent")
    parser.add_argument("--host", help="监听地址，默认 127.0.0.1（环境变量 CODELESS_HOST）")
    parser.add_argument("--port", type=int, help="端口，默认 8000（环境变量 CODELESS_PORT）")
    parser.add_argument("--local", action="store_true", help="不用 Docker，命令直接在本机执行（不隔离，仅限自用调试）")
    parser.add_argument("--data", help="数据目录，默认 ./data（环境变量 CODELESS_DATA_DIR）")
    args = parser.parse_args()

    if args.host:
        os.environ["CODELESS_HOST"] = args.host
    if args.port:
        os.environ["CODELESS_PORT"] = str(args.port)
    if args.local:
        os.environ["SANDBOX_BACKEND"] = "local"
    if args.data:
        os.environ["CODELESS_DATA_DIR"] = args.data

    import uvicorn

    from .config import Config
    from .server import build

    config = Config.load()
    if config.sandbox_backend not in ("docker", "local"):
        sys.exit(f"SANDBOX_BACKEND 只能是 docker 或 local，当前是 {config.sandbox_backend}")
    app, services = build(config)
    shown = "localhost" if config.host in ("127.0.0.1", "0.0.0.0") else config.host
    print(f"codeless 已启动：http://{shown}:{config.port}")
    print(f"沙箱：{services.sandboxes.describe()}；数据目录：{config.data_dir}")
    if config.host in ("0.0.0.0", "::") and not config.in_docker and "*" not in config.trusted_hosts:
        print("提示：从其他机器访问时，请把本机的域名或 IP 加到 CODELESS_TRUSTED_HOSTS，否则接口会拒绝请求。")
    if config.sandbox_backend == "local":
        print("⚠ 本地模式下 agent 的命令直接在这台机器上执行，没有隔离，请只在自己信任的环境里使用。")
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning", ws="websockets-sansio")


if __name__ == "__main__":
    main()
