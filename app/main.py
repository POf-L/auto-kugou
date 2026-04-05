"""
FastAPI 应用主入口
"""
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from app.config import HOST, PORT, DEBUG
from app.models import init_db
from app.api.auth import router as auth_router
from app.api.vip import router as vip_router
from app.api.admin import router as admin_router, validate_token
from app.tasks.scheduler import scheduler, setup_scheduler
from app.services.kugou_client import kugou_client

# 白名单路径（不需要认证）
_PUBLIC_PATHS = {
    "/health",
    "/",
    "/api/admin/status",
    "/api/admin/setup",
    "/api/admin/login",
}


async def _check_auth(request: Request, call_next):
    """认证中间件：检查 session token"""
    path = request.url.path

    # 白名单直接放行
    if path in _PUBLIC_PATHS:
        return await call_next(request)

    # 静态文件放行（CSS/JS等资源，不含敏感数据）
    if path.startswith("/static/"):
        return await call_next(request)

    # 从 Authorization header 或 query 参数获取 token
    token = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.query_params.get("token", "")

    if not validate_token(token):
        # API 请求返回 401 JSON
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "未授权访问", "code": "unauthorized"},
            )
        # 页面请求返回 401（前端会拦截并显示登录页）
        return JSONResponse(
            status_code=401,
            content={"detail": "unauthorized", "code": "unauthorized"},
        )

    return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("🎵 酷狗VIP自动领取工具 启动中...")
    # 初始化数据库
    await init_db()
    logger.info("✅ 数据库初始化完成")
    # 启动定时任务
    setup_scheduler()
    scheduler.start()
    logger.info("✅ 定时任务已启动")
    logger.info(f"🌐 Web界面: http://{HOST if HOST != '0.0.0.0' else '127.0.0.1'}:{PORT}")

    yield

    # 关闭清理
    scheduler.shutdown(wait=False)
    await kugou_client.close()
    logger.info("👋 服务已停止")


app = FastAPI(
    title="酷狗VIP自动领取工具",
    description="自动领取酷狗音乐VIP，支持多账号管理",
    version="1.0.0",
    lifespan=lifespan,
)

# 注册认证中间件
app.middleware("http")(_check_auth)

# 注册路由
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(vip_router)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    """返回主页"""
    return FileResponse("templates/index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "message": "酷狗VIP工具运行中"}


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=DEBUG,
        log_level="info",
    )
