"""
Vercel Serverless Function 入口
将 Vercel 的请求转发到 FastAPI 应用
"""
import sys
import os

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 确保数据库表存在
from app.models import init_db
import asyncio

try:
    loop = asyncio.get_event_loop()
    if loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, init_db())
            future.result()
    else:
        loop.run_until_complete(init_db())
except RuntimeError:
    asyncio.run(init_db())

# 暴露 FastAPI app 给 Vercel
from app.main import app

handler = app
