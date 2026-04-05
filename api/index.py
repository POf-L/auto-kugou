"""
Vercel Serverless Function 入口
将 Vercel 的请求转发到 FastAPI 应用
"""
import sys
import os

# 将项目根目录加入 Python 路径，确保 app 模块可以正确导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app

# Vercel Python runtime 会自动检测 ASGI app 变量
# 将 FastAPI app 暴露给 Vercel
handler = app
