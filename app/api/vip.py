"""
VIP 相关 API 路由
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.models import get_db, Account, init_db
from app.services import vip_service
from app.tasks.scheduler import (
    emit_event, auto_sign_all, auto_renew_all,
    refresh_all_tokens, get_recent_events,
    _sign_in_with_retry, _looks_like_auth_failure,
)
from app.config import CRON_SECRET


router = APIRouter(prefix="/api/vip", tags=["vip"])


@router.get("/status/{userid}")
async def get_vip_status(userid: str, db=Depends(get_db)):
    """获取指定账号的VIP状态"""
    result = db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return await vip_service.get_vip_status(acc.token, acc.userid)


@router.get("/status-raw/{userid}")
async def get_vip_status_raw(userid: str, db=Depends(get_db)):
    """获取VIP状态的原始API返回（调试用）"""
    result = db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    raw_result = await vip_service.get_vip_status(acc.token, acc.userid)
    return {"raw": raw_result.get("raw", {}), "active_vips": raw_result.get("active_vips", [])}


@router.get("/sign-info/{userid}")
async def get_sign_info(userid: str, db=Depends(get_db)):
    """获取签到信息"""
    result = db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    return await vip_service.get_sign_info(acc.token, acc.userid)


@router.post("/sign-in/{userid}")
async def manual_sign_in(userid: str, db=Depends(get_db)):
    """
    手动执行完整签到（畅听VIP + 概念VIP）。
    签到失败且疑似Token失效时，会自动刷新Token后重试一次。
    """
    result = db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    await emit_event("sign_in_start", userid, f"手动签到: {acc.nickname or userid}")
    res = await _sign_in_with_retry(db, acc, vip_service.do_sign_in, emit_events=True)
    if res.get("success"):
        await emit_event("sign_in_success", userid, res.get("message", "签到成功"), res)
    elif not _looks_like_auth_failure(res.get("message", "")):
        await emit_event("sign_in_failed", userid, res.get("message", "签到失败"))
    # 认证类失败已在 _sign_in_with_retry 内推送事件
    return res


@router.post("/sign-in/{userid}/tvip")
async def manual_sign_tvip(userid: str, db=Depends(get_db)):
    """
    仅执行畅听VIP签到。
    签到失败且疑似Token失效时，会自动刷新Token后重试一次。
    """
    result = db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    await emit_event("tvip_claim", userid, f"正在领取畅听VIP: {acc.nickname or userid}")
    res = await _sign_in_with_retry(db, acc, vip_service.do_sign_tvip_only, emit_events=True)
    if res.get("success"):
        if not res.get("skipped"):
            await emit_event("tvip_claim", userid, f"✅ {res.get('message', '畅听VIP领取成功')}", res)
        else:
            await emit_event("renew_skip", userid, res.get("message", "今日已签到"), res)
    elif not _looks_like_auth_failure(res.get("message", "")):
        await emit_event("sign_in_failed", userid, res.get("message", "畅听VIP领取失败"))
    return res


@router.post("/sign-in/{userid}/svip")
async def manual_sign_svip(userid: str, db=Depends(get_db)):
    """
    仅执行概念VIP升级（需要先有畅听VIP）。
    签到失败且疑似Token失效时，会自动刷新Token后重试一次。
    """
    result = db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    await emit_event("svip_claim", userid, f"正在升级概念VIP: {acc.nickname or userid}")
    res = await _sign_in_with_retry(db, acc, vip_service.do_upgrade_svip_only, emit_events=True)
    if res.get("success"):
        await emit_event("svip_claim", userid, f"👑 {res.get('message', '概念VIP升级成功')}", res)
    elif not _looks_like_auth_failure(res.get("message", "")):
        await emit_event("sign_in_failed", userid, res.get("message", "概念VIP升级失败"))
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
    """一键刷新全部账号Token（仅手动触发，不再被 Cron 定时调用）"""
    await refresh_all_tokens(emit_events=True)
    return {"success": True, "message": "Token刷新任务执行完成"}


@router.get("/logs/{userid}")
async def get_claim_logs(userid: str, limit: int = 20, db=Depends(get_db)):
    """获取账号的领取日志"""
    return vip_service.get_claim_logs(db, userid, limit)


# ========== 轮询接口（替代 SSE）==========

@router.get("/events")
async def poll_events(after_id: int = 0, limit: int = 50):
    """轮询获取进度事件（替代 SSE，Serverless 兼容）"""
    events = await get_recent_events(limit=limit, after_id=after_id)
    return {"events": events}


# ========== Vercel Cron 端点 ==========

@router.post("/cron/sign-in")
async def cron_sign_in(request: Request):
    """
    Cron 定时任务: 自动续领检查。

    Token 刷新策略已改为「按需触发」：
    - 不再单独调用 cron/refresh-token
    - 签到失败且疑似 Token 失效时，自动在内部刷新 Token 并重试
    """
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {CRON_SECRET}":
        raise HTTPException(status_code=403, detail="Forbidden")

    await auto_renew_all()
    return {"success": True, "message": "Cron: 过期VIP自动续领检查完成"}


@router.post("/cron/refresh-token")
async def cron_refresh_token(request: Request):
    """
    [已弃用] Vercel Cron: 定时刷新 Token。

    此端点保留以兼容已有配置，但实际不做任何操作。
    Token 刷新已改为签到失败时按需触发（见 /cron/sign-in 内部逻辑）。

    建议：可在 Vercel Dashboard 中移除此 Cron 调度以减少无效调用。
    """
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {CRON_SECRET}":
        raise HTTPException(status_code=403, detail="Forbidden")

    return {
        "success": True,
        "message": "Cron: Token定时刷新已禁用（改为签到失败时按需刷新）",
        "skipped": True,
    }
