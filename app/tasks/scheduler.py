"""
定时任务模块 - Token自动刷新 & VIP自动签到
"""
import asyncio
from datetime import datetime, timezone, timedelta
from loguru import logger
from sqlalchemy import select, update
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.models import AsyncSessionLocal, Account
from app.services.auth_service import refresh_token
from app.services.vip_service import do_sign_in


scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

# 进度事件队列（用于 SSE 推送到前端）
progress_events: list[dict] = []
MAX_EVENTS = 100


def emit_event(event_type: str, userid: str, message: str, data: dict = None):
    """向事件队列推送一条进度事件"""
    event = {
        "type": event_type,
        "userid": userid,
        "message": message,
        "data": data or {},
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    progress_events.append(event)
    if len(progress_events) > MAX_EVENTS:
        progress_events.pop(0)
    logger.info(f"[事件] {event_type} | {userid} | {message}")


async def _refresh_all_tokens():
    """刷新所有激活账号的Token"""
    logger.info(">>> 开始批量刷新Token")
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Account).where(Account.is_active == True))
        accounts = result.scalars().all()

        for acc in accounts:
            if not acc.token or not acc.userid:
                continue
            # 检查是否需要刷新（超过90分钟则刷新）
            if acc.last_token_refresh:
                elapsed = (datetime.now(timezone(timedelta(hours=8))) - acc.last_token_refresh).total_seconds()
                if elapsed < 5400:
                    continue
            try:
                emit_event("token_refresh", acc.userid, "正在刷新Token...")
                res = await refresh_token(acc.token, acc.userid)
                if res.get("success"):
                    await db.execute(
                        update(Account)
                        .where(Account.userid == acc.userid)
                        .values(
                            token=res.get("token", acc.token),
                            vip_type=res.get("vip_type", acc.vip_type),
                            last_token_refresh=datetime.now(timezone(timedelta(hours=8))),
                        )
                    )
                    await db.commit()
                    emit_event("token_refresh", acc.userid, "Token刷新成功", {"vip_type": res.get("vip_type")})
                else:
                    emit_event("token_refresh_failed", acc.userid, f"Token刷新失败: {res.get('message')}")
            except Exception as e:
                emit_event("token_refresh_failed", acc.userid, f"Token刷新异常: {e}")

    logger.info("<<< Token刷新完成")


async def _auto_sign_all():
    """对所有开启自动领取的账号执行签到"""
    logger.info(">>> 开始自动签到")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Account).where(Account.is_active == True, Account.auto_claim == True)
        )
        accounts = result.scalars().all()

        for acc in accounts:
            if not acc.token or not acc.userid:
                continue
            try:
                emit_event("sign_in_start", acc.userid, f"正在为账号 {acc.nickname or acc.userid} 执行签到...")
                res = await do_sign_in(acc.token, acc.userid, db)
                if res.get("success"):
                    msg = res.get("message", "签到完成")
                    emit_event("sign_in_success", acc.userid, msg, res)
                else:
                    emit_event("sign_in_failed", acc.userid, res.get("message", "签到失败"))
            except Exception as e:
                emit_event("sign_in_failed", acc.userid, f"签到异常: {e}")

    logger.info("<<< 自动签到完成")


def setup_scheduler():
    """配置并启动定时任务"""
    # Token刷新：每90分钟
    scheduler.add_job(_refresh_all_tokens, "interval", seconds=5400, id="token_refresh", replace_existing=True)
    # 自动签到：每天上午8点
    scheduler.add_job(_auto_sign_all, "cron", hour=8, minute=0, id="auto_sign", replace_existing=True)
    logger.info("定时任务已配置：Token刷新(每90分钟) + 自动签到(每日08:00)")
