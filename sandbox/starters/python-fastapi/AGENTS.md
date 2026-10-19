# 项目说明

Python FastAPI 应用，接口写在 main.py，页面放 static/。

- 安装依赖：`pip install -r requirements.txt`（沙箱里已预装 fastapi、uvicorn）
- 开发预览：`uvicorn main:app --host 0.0.0.0 --port $PORT --reload`
- 测试：`pytest`（测试文件放 tests/）
