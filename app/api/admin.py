"""
管理员访问控制模块

伪装方案：
- 登录页面看起来像普通账号登录（账号+密码）
- 「账号」输入框实际是输入管理密码的地方
- 「密码」输入框无论填什么都返回报错，迷惑访客
- 密码存在数据库 system_settings 表中，不依赖环境变量
- 使用 JWT 无状态 token（Serverless 兼容）
"""
import hashlib
from datetime import datetime, timezone, timedelta
import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import get_db, SystemSetting
from app.config import JWT_SECRET, JWT_ALGORITHM

router = APIRouter(prefix="/api/admin", tags=["admin"])

# JWT 有效期：7天
JWT_TTL = timedelta(days=7)


def _hash_password(password: str) -> str:
    """密码哈希（SHA-256 + 盐）"""
    salt = "kugou_vip_tool_2024"
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def _create_jwt_token() -> str:
    """签发 JWT token"""
    payload = {
        "sub": "admin",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + JWT_TTL,
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def validate_token(token: str | None) -> bool:
    """验证 JWT token 是否有效"""
    if not token:
        return False
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub") == "admin"
    except (pyjwt.ExpiredSignatureError, pyjwt.InvalidTokenError):
        return False


async def _get_setting(db: AsyncSession, key: str) -> str:
    """读取系统设置"""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else ""


async def _set_setting(db: AsyncSession, key: str, value: str):
    """写入系统设置"""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        row = SystemSetting(key=key, value=value)
        db.add(row)
    await db.commit()


class SetupRequest(BaseModel):
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


@router.get("/status")
async def admin_status(db: AsyncSession = Depends(get_db)):
    """检查是否已设置密码（首次访问引导用）"""
    pw_hash = await _get_setting(db, "admin_password")
    return {"initialized": bool(pw_hash)}


@router.post("/setup")
async def setup_password(req: SetupRequest, db: AsyncSession = Depends(get_db)):
    """首次设置管理密码"""
    existing = await _get_setting(db, "admin_password")
    if existing:
        raise HTTPException(status_code=400, detail="密码已设置，无法重复初始化")

    if not req.password or len(req.password) < 4:
        raise HTTPException(status_code=400, detail="密码长度不能少于4位")

    await _set_setting(db, "admin_password", _hash_password(req.password))
    token = _create_jwt_token()
    return {"success": True, "token": token}


@router.post("/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    伪装登录接口：
    - req.username -> 实际的管理密码
    - req.password -> 无论填什么都返回错误
    """
    if req.password:
        raise HTTPException(status_code=401, detail="账号或密码错误")

    if not req.username:
        raise HTTPException(status_code=401, detail="请输入账号")

    pw_hash = await _get_setting(db, "admin_password")
    if not pw_hash:
        raise HTTPException(status_code=500, detail="系统未初始化")

    if _hash_password(req.username) != pw_hash:
        raise HTTPException(status_code=401, detail="账号或密码错误")

    token = _create_jwt_token()
    return {"success": True, "token": token}
