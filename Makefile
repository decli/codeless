# 常用命令。第一次用：make install && make sandbox-image && make run

PY ?= .venv/bin/python

.PHONY: install web sandbox-image run dev web-dev test test-all lint up down

install:  ## 安装后端和前端依赖
	python3 -m venv .venv
	$(PY) -m pip install -e ".[browser,dev]"
	-$(PY) -m playwright install chromium
	cd web && npm ci

web:  ## 构建前端到 web/dist，由后端直接提供
	cd web && npm run build

sandbox-image:  ## 构建任务沙箱镜像（Python 3.12 + Node.js 22）
	docker build -f docker/sandbox.Dockerfile -t codeless-sandbox:latest .

run: web  ## 本机启动：http://localhost:8000
	$(PY) -m codeless

dev:  ## 只启动后端，配合 make web-dev 改前端
	$(PY) -m codeless

web-dev:  ## 前端开发服务器 http://localhost:5173，接口转发到 8000
	cd web && npm run dev

test:  ## 单元测试和端到端测试（假模型，本地进程沙箱，不需要 Docker）
	$(PY) -m pytest -m "not docker"

test-all:  ## 包括 Docker 沙箱测试
	$(PY) -m pytest

lint:
	$(PY) -m ruff check codeless tests
	cd web && npm run typecheck

up: sandbox-image  ## 用 Docker Compose 运行
	docker compose up -d --build

down:
	docker compose down
