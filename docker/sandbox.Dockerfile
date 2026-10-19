# 任务沙箱：Python 3.12 + Node.js 22，预装常用依赖并预热 npm 缓存，常见项目离线也能装好依赖。
#
#   docker build -f docker/sandbox.Dockerfile -t codeless-sandbox:latest .
#
# 国内网络可以换源：--build-arg APT_MIRROR=mirrors.aliyun.com
#                  --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#                  --build-arg NPM_REGISTRY=https://registry.npmmirror.com
# 企业内网有 HTTPS 中间人代理时，把根证书作为 secret 传入：--secret id=ca,src=/path/to/ca.crt
ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.12-slim

FROM ${NODE_IMAGE} AS node

FROM ${PYTHON_IMAGE}

ARG APT_MIRROR=deb.debian.org
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG NPM_REGISTRY=https://registry.npmjs.org

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack

RUN --mount=type=secret,id=ca,required=false \
    CA=/etc/ssl/certs/ca-certificates.crt; \
    if [ -f /run/secrets/ca ]; then cp /run/secrets/ca /tmp/ca.crt && chmod 644 /tmp/ca.crt && CA=/tmp/ca.crt; fi; \
    sed -i "s|http://deb.debian.org|https://${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::https::CAInfo="$CA" update \
    && apt-get -o Acquire::https::CAInfo="$CA" install -y --no-install-recommends \
       git curl ripgrep procps less jq unzip \
    && rm -rf /var/lib/apt/lists/* /tmp/ca.crt

# 常用 Python 包装到全局：Web 服务、HTTP、数据处理、绘图、测试
RUN --mount=type=secret,id=ca,required=false \
    [ -f /run/secrets/ca ] && export PIP_CERT=/run/secrets/ca; \
    pip install --no-cache-dir --index-url "$PIP_INDEX_URL" \
       pytest ruff fastapi "uvicorn[standard]" flask requests httpx jinja2 pydantic \
       numpy pandas matplotlib pillow python-multipart

# 起步模板：agent 可以直接 cp -r $CODELESS_STARTERS/<名字>/. . 作为起点
COPY sandbox/starters /opt/codeless/starters

# 预热 npm 缓存：按模板的锁文件装一遍再删掉，包留在 ~/.npm 里，之后 npm install 走缓存
RUN --mount=type=secret,id=ca,required=false \
    [ -f /run/secrets/ca ] && export NODE_EXTRA_CA_CERTS=/run/secrets/ca; \
    npm config set registry "$NPM_REGISTRY" \
    && for d in /opt/codeless/starters/*/; do \
         if [ -f "$d/package.json" ]; then \
           tmp=$(mktemp -d) && cp "$d"/package*.json "$tmp"/ \
           && (cd "$tmp" && npm ci --no-audit --no-fund) && rm -rf "$tmp"; \
         fi; \
       done \
    && npm config delete registry

ENV CODELESS_STARTERS=/opt/codeless/starters \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /workspace
CMD ["sleep", "infinity"]
