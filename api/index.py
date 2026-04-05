"""
Vercel Serverless Function 入口
将 Vercel 的请求转发到 FastAPI 应用
"""
import sys
import os

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 确保数据库表存在（同步调用，不需要 asyncio）
from app.models import init_db
init_db()

# 暴露 FastAPI app 给 Vercel
from app.main import app

handler = app
