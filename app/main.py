"""
FastAPI 应用主入口（Serverless 兼容版）

改造要点：
- 去掉 uvicorn 和 lifespan（Serverless 不需要）
- 数据库按需初始化（每个请求自动触发 init_db）
- 去掉 StaticFiles 挂载（Serverless 不支持）
- 去掉 scheduler 和后台任务
"""
import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse

from app.models import init_db
from app.api.admin import router as admin_router, validate_token
from app.api.auth import router as auth_router
from app.api.vip import router as vip_router

# 白名单路径（不需要认证）
_PUBLIC_PATHS = {
    "/health",
    "/",
    "/api/admin/status",
    "/api/admin/setup",
    "/api/admin/login",
}

# HTML 页面内容（内嵌，不再依赖文件系统）
_PAGE_HTML = None


async def _check_auth(request: Request, call_next):
    """认证中间件：检查 JWT token"""
    path = request.url.path

    # 白名单直接放行
    if path in _PUBLIC_PATHS:
        return await call_next(request)

    # Cron 端点单独验证（在 vip.py 中处理）
    if "/api/vip/cron/" in path:
        return await call_next(request)

    # 从 Authorization header 或 query 参数获取 token
    token = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.query_params.get("token", "")

    if not validate_token(token):
        return JSONResponse(
            status_code=401,
            content={"detail": "unauthorized", "code": "unauthorized"},
        )

    return await call_next(request)


app = FastAPI(
    title="酷狗VIP自动领取工具",
    description="自动领取酷狗音乐VIP，支持多账号管理",
    version="2.0.0",
)

# 注册认证中间件
app.middleware("http")(_check_auth)

# 注册路由
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(vip_router)


@app.get("/")
async def index():
    """返回主页"""
    global _PAGE_HTML
    if _PAGE_HTML is None:
        try:
            # 兼容本地和 Vercel 部署路径
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            html_path = os.path.join(base_dir, "templates", "index.html")
            with open(html_path, "r", encoding="utf-8") as f:
                _PAGE_HTML = f.read()
        except FileNotFoundError:
            _PAGE_HTML = "<h1>Page not found: templates/index.html</h1>"
    return HTMLResponse(_PAGE_HTML)


@app.get("/health")
async def health():
    return {"status": "ok", "message": "酷狗VIP工具运行中"}
