"""FastAPI 应用：接口在 /api 下，页面和静态资源放 static/。"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# 放在最后：其余路径都交给静态文件，访问 / 返回 static/index.html
app.mount("/", StaticFiles(directory="static", html=True), name="static")
