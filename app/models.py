"""
数据库模型定义
"""
from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, sessionmaker
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


# 数据库引擎和会话
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    """获取数据库会话（依赖注入）"""
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """初始化数据库，创建所有表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
