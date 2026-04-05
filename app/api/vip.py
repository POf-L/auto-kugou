"""
VIP 相关 API 路由
"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import get_db, Account, init_db
from app.services import vip_service
from app.tasks.scheduler import emit_event, auto_sign_all, refresh_all_tokens, get_recent_events
from app.config import CRON_SECRET


router = APIRouter(prefix="/api/vip", tags=["vip"])


@router.get("/status/{userid}")
async def get_vip_status(userid: str, db: AsyncSession = Depends(get_db)):
    """获取指定账号的VIP状态"""
    result = await db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return await vip_service.get_vip_status(acc.token, acc.userid)


@router.get("/status-raw/{userid}")
async def get_vip_status_raw(userid: str, db: AsyncSession = Depends(get_db)):
    """获取VIP状态的原始API返回（调试用）"""
    result = await db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    raw_result = await vip_service.get_vip_status(acc.token, acc.userid)
    return {"raw": raw_result.get("raw", {}), "active_vips": raw_result.get("active_vips", [])}


@router.get("/sign-info/{userid}")
async def get_sign_info(userid: str, db: AsyncSession = Depends(get_db)):
    """获取签到信息"""
    result = await db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return await vip_service.get_sign_info(acc.token, acc.userid)


@router.post("/sign-in/{userid}")
async def manual_sign_in(userid: str, db: AsyncSession = Depends(get_db)):
    """手动执行签到"""
    result = await db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    await emit_event("sign_in_start", userid, f"手动签到: {acc.nickname or userid}")
    res = await vip_service.do_sign_in(acc.token, acc.userid, db)
    if res.get("success"):
        await emit_event("sign_in_success", userid, res.get("message", "签到成功"), res)
    else:
        await emit_event("sign_in_failed", userid, res.get("message", "签到失败"))
    return res


@router.post("/sign-in-all")
async def sign_in_all():
    """一键对所有账号执行签到（Serverless: 同步执行，因为后台任务无法持久化）"""
    # 在 Serverless 中不能 create_task（请求结束后函数会被冻结）
    # 所以改为直接执行（Vercel Function 最长 60s，够用）
    await auto_sign_all()
    return {"success": True, "message": "签到任务执行完成"}


@router.post("/refresh-token-all")
async def refresh_token_all():
    """一键刷新全部账号Token"""
    await refresh_all_tokens()
    return {"success": True, "message": "Token刷新任务执行完成"}


@router.get("/logs/{userid}")
async def get_claim_logs(userid: str, limit: int = 20, db: AsyncSession = Depends(get_db)):
    """获取账号的领取日志"""
    return await vip_service.get_claim_logs(db, userid, limit)


# ========== 轮询接口（替代 SSE）==========

@router.get("/events")
async def poll_events(after_id: int = 0, limit: int = 50):
    """轮询获取进度事件（替代 SSE，Serverless 兼容）"""
    events = await get_recent_events(limit=limit, after_id=after_id)
    return {"events": events}


# ========== Vercel Cron 端点 ==========

@router.post("/cron/sign-in")
async def cron_sign_in(request: Request):
    """Vercel Cron: 每日自动签到（由 vercel.json cron 配置触发）"""
    # 验证 Cron 密钥
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {CRON_SECRET}":
        raise HTTPException(status_code=403, detail="Forbidden")

    await auto_sign_all()
    return {"success": True, "message": "Cron: 自动签到完成"}


@router.post("/cron/refresh-token")
async def cron_refresh_token(request: Request):
    """Vercel Cron: 定时刷新 Token"""
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {CRON_SECRET}":
        raise HTTPException(status_code=403, detail="Forbidden")

    await refresh_all_tokens()
    return {"success": True, "message": "Cron: Token刷新完成"}
