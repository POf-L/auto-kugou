"""
数据库模型定义
支持 Vercel Postgres (Neon) 和本地 SQLite 双模式
使用同步 SQLAlchemy（Serverless 兼容）
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from app.config import DATABASE_URL

# 中国时区 UTC+8
_CST = timezone(timedelta(hours=8))


def _now():
    """当前中国本地时间"""
    return datetime.now(_CST)


class Base(DeclarativeBase):
    pass


class Account(Base):
    """酷狗账号表"""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    userid = Column(String(64), unique=True, nullable=False, comment="酷狗用户ID")
    nickname = Column(String(128), default="", comment="用户昵称")
    avatar = Column(String(512), default="", comment="头像URL")
    mobile = Column(String(20), default="", comment="手机号（脱敏）")
    token = Column(Text, default="", comment="认证Token")
    vip_type = Column(Integer, default=0, comment="VIP类型 0=非VIP 1=VIP 2=SVIP")
    vip_expire_time = Column(DateTime, nullable=True, comment="VIP过期时间")
    login_type = Column(String(20), default="", comment="登录方式：password/sms/qrcode/token")
    is_active = Column(Boolean, default=True, comment="账号是否激活")
    auto_claim = Column(Boolean, default=True, comment="是否自动领取VIP")
    last_claim_time = Column(DateTime, nullable=True, comment="最后领取时间")
    last_token_refresh = Column(DateTime, nullable=True, comment="最后Token刷新时间")
    created_at = Column(DateTime, default=_now, comment="创建时间")
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class SystemSetting(Base):
    """系统设置表（KV存储）"""
    __tablename__ = "system_settings"

    key = Column(String(64), primary_key=True, comment="设置键")
    value = Column(Text, default="", comment="设置值")
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class ClaimLog(Base):
    """VIP领取日志表"""
    __tablename__ = "claim_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    userid = Column(String(64), nullable=False, comment="用户ID")
    status = Column(String(20), nullable=False, comment="领取状态：success/failed/skipped")
    message = Column(Text, default="", comment="结果消息")
    claim_type = Column(String(32), default="", comment="领取类型")
    created_at = Column(DateTime, default=_now)


class ProgressEvent(Base):
    """进度事件表（替代内存队列，Serverless 兼容）"""
    __tablename__ = "progress_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(32), nullable=False, comment="事件类型")
    userid = Column(String(64), default="", comment="用户ID")
    message = Column(Text, default="", comment="事件消息")
    data = Column(Text, default="{}", comment="附加数据 JSON")
    created_at = Column(DateTime, default=_now)


# ========== 数据库引擎 ==========
if DATABASE_URL.startswith(("postgres://", "postgresql://")):
    # Vercel Postgres (Neon) 模式
    # Neon 的 pgbouncer 端口需要 pool_size=1 避免 prepared statement 错误
    url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    engine = create_engine(url, pool_size=1, pool_recycle=300)
else:
    # 本地 SQLite 模式
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    """获取数据库会话（依赖注入）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_db_initialized = False


def init_db():
    """初始化数据库，创建所有表"""
    global _db_initialized
    if _db_initialized:
        return
    Base.metadata.create_all(bind=engine)
    _db_initialized = True
