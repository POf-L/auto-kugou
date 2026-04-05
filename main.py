"""
项目启动脚本 - 直接运行此文件启动服务
"""
import uvicorn
from app.config import HOST, PORT, DEBUG

if __name__ == "__main__":
    print("=" * 50)
    print("  酷狗VIP自动领取工具")
    print("=" * 50)
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=DEBUG,
        log_level="info",
    )
