# 编排服务镜像：Web 界面 + agent + 预览代理。任务沙箱由它通过宿主机的 Docker 拉起。
#
#   docker compose up -d --build
#
# 国内网络可以换源：--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#                  --build-arg NPM_REGISTRY=https://registry.npmmirror.com
# 不需要浏览器自检（截图、抓控制台报错）可以 --build-arg WITH_BROWSER=0，镜像小 400 MB 左右。
# 企业内网有 HTTPS 中间人代理时，把根证书作为 secret 传入：--secret id=ca,src=/path/to/ca.crt
ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.12-slim
ARG DOCKER_CLI_IMAGE=docker:27-cli

FROM ${NODE_IMAGE} AS web
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=secret,id=ca,required=false \
    [ -f /run/secrets/ca ] && export NODE_EXTRA_CA_CERTS=/run/secrets/ca; \
    npm ci --no-audit --no-fund --registry "$NPM_REGISTRY"
COPY web/ ./
RUN npm run build

FROM ${DOCKER_CLI_IMAGE} AS docker-cli

FROM ${PYTHON_IMAGE}
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG WITH_BROWSER=1

COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
RUN --mount=type=secret,id=ca,required=false \
    CA=/etc/ssl/certs/ca-certificates.crt; \
    if [ -f /run/secrets/ca ]; then cp /run/secrets/ca /tmp/ca.crt && chmod 644 /tmp/ca.crt && CA=/tmp/ca.crt; fi; \
    apt-get -o Acquire::https::CAInfo="$CA" update \
    && apt-get -o Acquire::https::CAInfo="$CA" install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* /tmp/ca.crt

WORKDIR /app
COPY pyproject.toml README.md ./
COPY codeless ./codeless
RUN --mount=type=secret,id=ca,required=false \
    [ -f /run/secrets/ca ] && export PIP_CERT=/run/secrets/ca NODE_EXTRA_CA_CERTS=/run/secrets/ca; \
    pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -e ".[browser]" \
    && if [ "$WITH_BROWSER" = "1" ]; then python -m playwright install --with-deps chromium; \
       else pip uninstall -y playwright; fi \
    && rm -rf /var/lib/apt/lists/*

COPY sandbox ./sandbox
COPY --from=web /web/dist ./web/dist

ENV CODELESS_HOST=0.0.0.0 \
    CODELESS_DATA_DIR=/app/data \
    CODELESS_IN_DOCKER=1 \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "-m", "codeless"]
