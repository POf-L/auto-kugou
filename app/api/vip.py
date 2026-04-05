"""
VIP 相关 API 路由
"""
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import get_db, Account
from app.services import vip_service
from app.tasks.scheduler import progress_events, emit_event, _auto_sign_all, _refresh_all_tokens


router = APIRouter(prefix="/api/vip", tags=["vip"])


def _get_account_by_userid(accounts, userid: str):
    return next((a for a in accounts if a.userid == userid), None)


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
    emit_event("sign_in_start", userid, f"手动签到: {acc.nickname or userid}")
    res = await vip_service.do_sign_in(acc.token, acc.userid, db)
    if res.get("success"):
        emit_event("sign_in_success", userid, res.get("message", "签到成功"), res)
    else:
        emit_event("sign_in_failed", userid, res.get("message", "签到失败"))
    return res


@router.post("/sign-in-all")
async def sign_in_all():
    """一键对所有账号执行签到"""
    asyncio.create_task(_auto_sign_all())
    return {"success": True, "message": "已触发全部账号签到，请查看进度日志"}


@router.post("/refresh-token-all")
async def refresh_token_all():
    """一键刷新全部账号Token"""
    asyncio.create_task(_refresh_all_tokens())
    return {"success": True, "message": "已触发Token刷新，请查看进度日志"}


@router.get("/logs/{userid}")
async def get_claim_logs(userid: str, limit: int = 20, db: AsyncSession = Depends(get_db)):
    """获取账号的领取日志"""
    return await vip_service.get_claim_logs(db, userid, limit)


@router.get("/events")
async def sse_events():
    """
    Server-Sent Events 接口，实时推送进度事件到前端
    """
    async def event_generator():
        last_index = max(0, len(progress_events) - 1)
        # 先推送最近的5条历史事件
        for event in progress_events[-5:]:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        while True:
            if len(progress_events) > last_index:
                for event in progress_events[last_index:]:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                last_index = len(progress_events)
            # 每2秒发送心跳
            yield f": heartbeat\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
