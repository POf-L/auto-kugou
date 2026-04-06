"""
事件与定时任务模块（Serverless 兼容版）

改造要点：
- 去掉 APScheduler，所有进度事件存入数据库
- 由外部计划任务触发 /api/vip/cron/sign-in 和 /api/vip/cron/refresh-token
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


def _ensure_cst(dt: datetime | None) -> datetime | None:
    """把数据库读出的时间统一转换到中国时区，兼容 naive/aware datetime"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).astimezone(_CST)
    return dt.astimezone(_CST)


def _format_cst(dt: datetime | None) -> str:
    """格式化为中国时间字符串"""
    normalized = _ensure_cst(dt)
    return normalized.strftime("%Y-%m-%d %H:%M:%S") if normalized else ""


def _looks_like_auth_failure(message: str) -> bool:
    text = (message or "").lower()
    keywords = ("登录失败", "未登录", "token", "unauthorized", "auth")
    return any(k in text for k in keywords)


async def _refresh_account_token(db, acc: Account, *, emit_events: bool = True) -> bool:
    """刷新单个账号 Token，支持静默模式（自动续领内部回退）"""
    try:
        if emit_events:
            await emit_event("token_refresh", acc.userid, "正在刷新Token...")

        from app.services.auth_service import refresh_token

        res = await refresh_token(acc.token, acc.userid)
        if not res.get("success"):
            if emit_events:
                await emit_event("token_refresh_failed", acc.userid, f"Token刷新失败: {res.get('message')}")
            else:
                logger.warning(f"账号 {acc.userid} Token刷新失败: {res.get('message')}")
            return False

        new_token = res.get("token", acc.token)
        new_vip_type = res.get("vip_type", acc.vip_type)
        refresh_time = datetime.now(_CST)
        db.execute(
            update(Account)
            .where(Account.userid == acc.userid)
            .values(
                token=new_token,
                vip_type=new_vip_type,
                last_token_refresh=refresh_time,
            )
        )
        db.commit()
        acc.token = new_token
        acc.vip_type = new_vip_type
        acc.last_token_refresh = refresh_time

        if emit_events:
            await emit_event("token_refresh", acc.userid, "Token刷新成功", {"vip_type": new_vip_type})
        return True
    except Exception as e:
        if emit_events:
            await emit_event("token_refresh_failed", acc.userid, f"Token刷新异常: {e}")
        else:
            logger.warning(f"账号 {acc.userid} Token刷新异常: {e}")
        return False


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
                    "timestamp": _format_cst(e.created_at),
                }
                for e in reversed(events)
            ]
        finally:
            db.close()
    except Exception as e:
        logger.error(f"获取事件失败: {e}")
        return []


async def refresh_all_tokens(emit_events: bool = True):
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
                last_refresh = _ensure_cst(acc.last_token_refresh)
                elapsed = (datetime.now(_CST) - last_refresh).total_seconds()
                if elapsed < 5400:
                    continue
            await _refresh_account_token(db, acc, emit_events=emit_events)
    finally:
        db.close()

    logger.info("<<< Token刷新完成")


async def auto_sign_all():
    """对所有开启自动领取的账号执行签到（手动批量使用）"""
    logger.info(">>> 开始批量签到")
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

    logger.info("<<< 批量签到完成")


async def auto_renew_all():
    """对所有开启自动领取的账号执行“过期后自动续领”检查"""
    logger.info(">>> 开始自动续领检查")
    db = SessionLocal()
    try:
        result = db.execute(
            select(Account).where(Account.is_active == True, Account.auto_claim == True)
        )
        accounts = result.scalars().all()

        from app.services import vip_service

        for acc in accounts:
            if not acc.token or not acc.userid:
                continue
            try:
                renew_check = await vip_service.should_auto_renew(acc.token, acc.userid)
                if not renew_check.get("success"):
                    refreshed = await _refresh_account_token(db, acc, emit_events=False)
                    if refreshed:
                        renew_check = await vip_service.should_auto_renew(acc.token, acc.userid)

                if not renew_check.get("should_renew"):
                    expire_tip = renew_check.get("expire_time", "")
                    logger.info(
                        f"账号 {acc.nickname or acc.userid} VIP仍有效，跳过自动续领"
                        + (f"（到期：{expire_tip}）" if expire_tip else "")
                    )
                    continue

                if not renew_check.get("success"):
                    await emit_event(
                        "renew_check_failed",
                        acc.userid,
                        f"VIP状态检查失败，继续尝试自动续领: {renew_check.get('message', '未知错误')}",
                    )

                await emit_event(
                    "renew_start",
                    acc.userid,
                    f"检测到 VIP 过期，正在自动续领 {acc.nickname or acc.userid}...",
                )
                res = await vip_service.do_sign_in(acc.token, acc.userid, db)
                if (not res.get("success")) and _looks_like_auth_failure(res.get("message", "")):
                    refreshed = await _refresh_account_token(db, acc, emit_events=False)
                    if refreshed:
                        res = await vip_service.do_sign_in(acc.token, acc.userid, db)
                if res.get("success"):
                    if res.get("skipped"):
                        await emit_event("renew_skip", acc.userid, res.get("message", "今日已签到，跳过自动续领"), res)
                    else:
                        await emit_event("renew_success", acc.userid, res.get("message", "自动续领完成"), res)
                else:
                    await emit_event("renew_failed", acc.userid, res.get("message", "自动续领失败"))
            except Exception as e:
                await emit_event("renew_failed", acc.userid, f"自动续领异常: {e}")
    finally:
        db.close()

    logger.info("<<< 自动续领检查完成")
