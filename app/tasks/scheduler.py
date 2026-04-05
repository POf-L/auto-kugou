"""
事件与定时任务模块（Serverless 兼容版）

改造要点：
- 去掉 APScheduler，所有进度事件存入数据库
- Vercel Cron 触发 /api/cron/sign-in 和 /api/cron/refresh-token
- 进度事件通过数据库轮询获取
- 数据库操作使用同步 Session
"""
import json
from datetime import datetime, timezone, timedelta
from loguru import logger
from sqlalchemy import select, update, delete

from app.models import SessionLocal, Account, ProgressEvent

_CST = timezone(timedelta(hours=8))
MAX_EVENTS = 100


async def emit_event(event_type: str, userid: str, message: str, data: dict = None):
    """向数据库写入一条进度事件"""
    try:
        db = SessionLocal()
        try:
            event = ProgressEvent(
                event_type=event_type,
                userid=userid,
                message=message,
                data=json.dumps(data or {}, ensure_ascii=False),
            )
            db.add(event)
            db.commit()

            # 清理旧事件
            result = db.execute(
                select(ProgressEvent).order_by(ProgressEvent.created_at.desc()).offset(MAX_EVENTS)
            )
            old_events = result.scalars().all()
            for old in old_events:
                db.delete(old)
            if old_events:
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"写入事件失败: {e}")


async def get_recent_events(limit: int = 50, after_id: int = 0) -> list[dict]:
    """获取最近的事件（用于轮询）"""
    try:
        db = SessionLocal()
        try:
            if after_id:
                result = db.execute(
                    select(ProgressEvent)
                    .where(ProgressEvent.id > after_id)
                    .order_by(ProgressEvent.id.desc())
                    .limit(limit)
                )
            else:
                result = db.execute(
                    select(ProgressEvent)
                    .order_by(ProgressEvent.id.desc())
                    .limit(limit)
                )
            events = result.scalars().all()
            return [
                {
                    "id": e.id,
                    "type": e.event_type,
                    "userid": e.userid,
                    "message": e.message,
                    "data": json.loads(e.data) if e.data else {},
                    "timestamp": e.created_at.strftime("%Y-%m-%d %H:%M:%S") if e.created_at else "",
                }
                for e in reversed(events)
            ]
        finally:
            db.close()
    except Exception as e:
        logger.error(f"获取事件失败: {e}")
        return []


async def refresh_all_tokens():
    """刷新所有激活账号的 Token"""
    logger.info(">>> 开始批量刷新Token")
    db = SessionLocal()
    try:
        result = db.execute(select(Account).where(Account.is_active == True))
        accounts = result.scalars().all()

        for acc in accounts:
            if not acc.token or not acc.userid:
                continue
            if acc.last_token_refresh:
                elapsed = (datetime.now(_CST) - acc.last_token_refresh).total_seconds()
                if elapsed < 5400:
                    continue
            try:
                await emit_event("token_refresh", acc.userid, "正在刷新Token...")
                from app.services.auth_service import refresh_token
                res = await refresh_token(acc.token, acc.userid)
                if res.get("success"):
                    db.execute(
                        update(Account)
                        .where(Account.userid == acc.userid)
                        .values(
                            token=res.get("token", acc.token),
                            vip_type=res.get("vip_type", acc.vip_type),
                            last_token_refresh=datetime.now(_CST),
                        )
                    )
                    db.commit()
                    await emit_event("token_refresh", acc.userid, "Token刷新成功", {"vip_type": res.get("vip_type")})
                else:
                    await emit_event("token_refresh_failed", acc.userid, f"Token刷新失败: {res.get('message')}")
            except Exception as e:
                await emit_event("token_refresh_failed", acc.userid, f"Token刷新异常: {e}")
    finally:
        db.close()

    logger.info("<<< Token刷新完成")


async def auto_sign_all():
    """对所有开启自动领取的账号执行签到"""
    logger.info(">>> 开始自动签到")
    db = SessionLocal()
    try:
        result = db.execute(
            select(Account).where(Account.is_active == True, Account.auto_claim == True)
        )
        accounts = result.scalars().all()

        for acc in accounts:
            if not acc.token or not acc.userid:
                continue
            try:
                await emit_event("sign_in_start", acc.userid, f"正在为账号 {acc.nickname or acc.userid} 执行签到...")
                from app.services.vip_service import do_sign_in
                res = await do_sign_in(acc.token, acc.userid, db)
                if res.get("success"):
                    msg = res.get("message", "签到完成")
                    await emit_event("sign_in_success", acc.userid, msg, res)
                else:
                    await emit_event("sign_in_failed", acc.userid, res.get("message", "签到失败"))
            except Exception as e:
                await emit_event("sign_in_failed", acc.userid, f"签到异常: {e}")
    finally:
        db.close()

    logger.info("<<< 自动签到完成")
