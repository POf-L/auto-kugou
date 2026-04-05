"""
项目启动脚本 - 直接运行此文件启动服务
"""
import uvicorn
from app.config import HOST, PORT, DEBUG
from app.models import init_db

if __name__ == "__main__":
    print("=" * 50)
    print("  酷狗VIP自动领取工具")
    print("=" * 50)
    # 初始化数据库（创建表）
    init_db()
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=DEBUG,
        log_level="info",
    )
