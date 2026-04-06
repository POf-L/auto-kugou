"""
事件与定时任务模块（Serverless 兼容版）

改造要点：
- 去掉 APScheduler，所有进度事件存入数据库
- 由外部计划任务触发 /api/vip/cron/sign-in（Token刷新改为按需触发）
- 进度事件通过数据库轮询获取
- 数据库操作使用同步 Session

Token 刷新策略：
- 不再定时/固定刷新 Token（避免触发酷狗风控）
- 仅在签到失败且疑似认证问题时，才自动刷新 Token 并重试签到
"""
import json
from datetime import datetime, timezone, timedelta
from loguru import logger
from sqlalchemy import select, update, delete

from app.models import SessionLocal, Account, ProgressEvent

_CST = timezone(timedelta(hours=8))
MAX_EVENTS = 50


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
    keywords = ("登录失败", "未登录", "token", "unauthorized", "auth", "token无效", "过期")
    return any(k in text for k in text)


async def _sign_in_with_retry(db, acc: Account, sign_fn, *, emit_events: bool = True) -> dict:
    """
    执行签到，失败且像认证问题时自动刷新 Token 后重试一次。

    每个关键步骤都会通过 emit_event 的 data 字段记录详细信息，
    用于故障排查（API返回、错误码、Token状态等）。

    Args:
        db: 数据库 session
        acc: Account 实例（会被就地更新 token）
        sign_fn: 异步签到函数，签收 (token, userid, db) → dict
        emit_events: 是否推送事件到前端

    Returns:
        签到结果 dict（含 success/message 等字段）
    """
    sign_fn_name = getattr(sign_fn, "__name__", str(sign_fn))

    # ========== 第1次尝试：直接用现有 Token 签到 ==========
    if emit_events:
        await emit_event(
            "sign_in_start", acc.userid,
            f"开始签到 [函数: {sign_fn_name}]",
            {
                "attempt": 1,
                "mode": "direct",
                "userid": acc.userid,
                "nickname": acc.nickname or "",
                "token_preview": _mask_token(acc.token),
                "last_token_refresh": _format_cst(acc.last_token_refresh),
                "vip_type": acc.vip_type,
            },
        )

    res = await sign_fn(acc.token, acc.userid, db)

    if res.get("success"):
        if emit_events:
            await emit_event(
                "sign_in_success", acc.userid,
                f"✅ 签到成功: {res.get('message', '')}",
                {"attempt": 1, "result": res},
            )
        return res

    # ========== 签到失败：记录第1次失败的详细数据 ==========
    fail_msg = res.get("message", "")
    is_auth_fail = _looks_like_auth_failure(fail_msg)

    if emit_events:
        await emit_event(
            "sign_in_detail", acc.userid,
            f"📋 第1次签到失败详情",
            {
                "attempt": 1,
                "success": False,
                "is_auth_failure": is_auth_fail,
                "error_message": fail_msg,
                "full_result": res,
                "token_preview": _mask_token(acc.token),
                "will_retry": is_auth_fail,
            },
        )

    if not is_auth_fail:
        # 非认证问题（如已领取、达上限等），直接返回原结果
        if emit_events:
            await emit_event(
                "sign_in_failed", acc.userid,
                fail_msg,
                {"attempt": 1, "non_auth_error": True, "result": res},
            )
        return res

    # ========== 认证问题：刷新 Token ==========
    if emit_events:
        await emit_event(
            "token_refresh", acc.userid,
            f"🔄 签到疑似Token失效({fail_msg})，正在刷新Token重试...",
            {
                "trigger": "sign_in_auth_failure",
                "original_error": fail_msg,
                "old_token_preview": _mask_token(acc.token),
            },
        )

    refreshed = await _refresh_account_token(db, acc, emit_events=emit_events)

    # ========== 刷新后第2次尝试 ==========
    if refreshed:
        if emit_events:
            await emit_event(
                "sign_in_start", acc.userid,
                f"使用新Token重新签到 [函数: {sign_fn_name}]",
                {
                    "attempt": 2,
                    "mode": "after_refresh",
                    "token_preview": _mask_token(acc.token),
                    "refresh_time": _format_cst(acc.last_token_refresh),
                    "vip_type_after_refresh": acc.vip_type,
                },
            )
        res = await sign_fn(acc.token, acc.userid, db)

        if emit_events:
            if res.get("success"):
                await emit_event(
                    "sign_in_success", acc.userid,
                    f"✅ Token刷新后签到成功: {res.get('message', '')}",
                    {"attempt": 2, "token_refreshed": True, "result": res},
                )
            else:
                await emit_event(
                    "sign_in_failed", acc.userid,
                    f"⚠️ Token刷新后仍失败: {res.get('message', '')}",
                    {
                        "attempt": 2,
                        "token_refreshed": True,
                        "is_auth_failure": _looks_like_auth_failure(res.get("message", "")),
                        "full_result": res,
                    },
                )
    else:
        # Token 刷新也失败了
        if emit_events:
            await emit_event(
                "sign_in_failed", acc.userid,
                f"❌ 签到失败且Token刷新也失败: {fail_msg}",
                {
                    "attempt": 1,
                    "token_refreshed": False,
                    "original_sign_in_error": fail_msg,
                    "retry_skipped_reason": "token_refresh_failed",
                },
            )

    return res


def _mask_token(token: str | None, show_len: int = 8) -> str:
    """脱敏显示 Token：只显示前 show_len 位 + ... + 后4位"""
    if not token or len(token) < 12:
        return "***" if token else "(空)"
    return f"{token[:show_len]}...{token[-4:]}"


async def _refresh_account_token(db, acc: Account, *, emit_events: bool = True) -> bool:
    """刷新单个账号 Token，支持静默模式（按需刷新时内部调用）"""
    try:
        if emit_events:
            await emit_event("token_refresh", acc.userid, "正在刷新Token...")

        from app.services.auth_service import refresh_token

        res = await refresh_token(acc.token, acc.userid)
        if not res.get("success"):
            msg = res.get('message', '未知错误')
            if emit_events:
                await emit_event("token_refresh_failed", acc.userid, f"Token刷新失败: {msg}")
            else:
                logger.warning(f"账号 {acc.userid} Token刷新失败: {msg}")
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


# ============================================================
#  Token 刷新（仅保留手动接口，不再被 Cron 定时调用）
# ============================================================

async def refresh_all_tokens(emit_events: bool = True):
    """
    手动批量刷新所有激活账号的 Token。

    注意：此函数仅通过手动按钮 /api/vip/refresh-token-all 触发，
    不再被 Cron 定时任务调用。Token 刷新已改为「签到失败时按需触发」。
    """
    logger.info(">>> 开始批量刷新Token（手动触发）")
    db = SessionLocal()
    try:
        result = db.execute(select(Account).where(Account.is_active == True))
        accounts = result.scalars().all()

        for acc in accounts:
            if not acc.token or not acc.userid:
                continue

            # 避免短时间内重复刷新同一账号（6小时冷却）
            if acc.last_token_refresh:
                last_refresh = _ensure_cst(acc.last_token_refresh)
                elapsed = (datetime.now(_CST) - last_refresh).total_seconds()
                if elapsed < 21600:  # 6小时
                    if emit_events:
                        await emit_event("token_refresh", acc.userid,
                            f"跳过（{int(elapsed/3600)}小时内已刷新过）")
                    continue

            refreshed = await _refresh_account_token(db, acc, emit_events=emit_events)
            if not refreshed and emit_events:
                logger.info(f"账号 {acc.userid} Token 刷新未成功")
    finally:
        db.close()

    logger.info("<<< 手动Token刷新完成")


# ============================================================
#  批量签到 & 自动续领（均内置签到失败→刷新→重试逻辑）
# ============================================================

async def auto_sign_all():
    """
    对所有开启自动领取的账号执行签到（手动批量 / Cron 使用）。
    签到失败时会自动判断是否为认证问题，是则刷新Token后重试一次。
    每个账号的签到过程都有详细的诊断日志。
    """
    logger.info(">>> 开始批量签到")
    db = SessionLocal()
    try:
        result = db.execute(
            select(Account).where(Account.is_active == True, Account.auto_claim == True)
        )
        accounts = result.scalars().all()

        from app.services.vip_service import do_sign_in

        for acc in accounts:
            if not acc.token or not acc.userid:
                continue
            try:
                # _sign_in_with_retry 内部会推送 sign_in_start/detail/success/failed 等所有事件
                # 这里不再重复推，直接调用即可
                res = await _sign_in_with_retry(db, acc, do_sign_in, emit_events=True)

                if res.get("success") and not _looks_like_auth_failure(res.get("message", "")):
                    # 成功且非认证类：补充一条汇总（_sign_in_with_retry 内已推过 success，这里不重复了）
                    pass
            except Exception as e:
                await emit_event("sign_in_failed", acc.userid, f"❌ 签到异常: {e}",
                    {"exception_type": type(e).__name__, "exception_msg": str(e)})
    finally:
        db.close()

    logger.info("<<< 批量签到完成")


async def auto_renew_all():
    """
    对所有开启自动领取的账号执行"过期后自动续领"检查。

    流程：
    1. 检查 VIP 是否需要续领（should_auto_renew）→ 记录状态详情
    2. 如需续领，执行签到 → 内置失败→刷新→重试逻辑
    3. 所有步骤均有详细 data 日志

    不再主动/提前刷新 Token——Token 只在真正需要时（签到失败）才会刷新。
    """
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
                # 第一步：用现有 Token 查询 VIP 状态，记录详情用于排查
                renew_check = await vip_service.should_auto_renew(acc.token, acc.userid)

                await emit_event(
                    "vip_status_check", acc.userid,
                    f"VIP状态检查: {'需续领' if renew_check.get('should_renew') else '仍有效' if renew_check.get('success') else '查询失败'}",
                    {
                        "userid": acc.userid,
                        "nickname": acc.nickname or "",
                        "should_renew": renew_check.get("should_renew", False),
                        "check_success": renew_check.get("success", False),
                        "check_message": renew_check.get("message", ""),
                        "expire_time": renew_check.get("expire_time", ""),
                        "token_preview": _mask_token(acc.token),
                        "will_proceed_to_sign_in": bool(renew_check.get("should_renew")),
                    },
                )

                # 如果状态查询本身失败（可能是 Token 问题），先尝试刷新再查一次
                if not renew_check.get("success"):
                    refreshed = await _refresh_account_token(db, acc, emit_events=False)
                    if refreshed:
                        renew_check = await vip_service.should_auto_renew(acc.token, acc.userid)
                        await emit_event(
                            "vip_status_check", acc.userid,
                            f"Token刷新后重新检查VIP状态: {'需续领' if renew_check.get('should_renew') else '仍有效'}",
                            {
                                "after_token_refresh": True,
                                "should_renew": renew_check.get("should_renew", False),
                                "check_message": renew_check.get("message", ""),
                                "expire_time": renew_check.get("expire_time", ""),
                            },
                        )
                    else:
                        # 连查询都失败且刷新也不行，跳过这个账号
                        await emit_event(
                            "renew_failed", acc.userid,
                            f"❌ 无法获取VIP状态(Token可能已失效)，请重新登录",
                            {
                                "reason": "vip_status_query_failed_and_token_refresh_failed",
                                "original_error": renew_check.get("message", ""),
                                "token_preview": _mask_token(acc.token),
                            },
                        )
                        continue

                if not renew_check.get("should_renew"):
                    # VIP 仍有效，记录一下当前状态供参考
                    continue

                await emit_event(
                    "renew_start",
                    acc.userid,
                    f"检测到 VIP 过期/需续领，正在自动签到 {acc.nickname or acc.userid}...",
                    {
                        "expire_time": renew_check.get("expire_time", ""),
                        "check_message": renew_check.get("message", ""),
                    },
                )

                # 执行签到（内置失败→刷新→重试逻辑，内部会推详细事件）
                res = await _sign_in_with_retry(db, acc, vip_service.do_sign_in, emit_events=True)

                if res.get("success"):
                    if res.get("skipped"):
                        await emit_event("renew_skip", acc.userid, res.get("message", "今日已签到，跳过"),
                            {"result": res})
                    else:
                        await emit_event("renew_success", acc.userid, res.get("message", "自动续领完成"),
                            {"result": res})
                elif not _looks_like_auth_failure(res.get("message", "")):
                    # 非认证类失败
                    await emit_event("renew_failed", acc.userid, res.get("message", "自动续领失败"),
                        {"result": res})
                # 认证类失败已在 _sign_in_with_retry 内处理了事件
            except Exception as e:
                await emit_event("renew_failed", acc.userid, f"❌ 自动续领异常: {e}",
                    {"exception_type": type(e).__name__, "exception_msg": str(e)})
    finally:
        db.close()

    logger.info("<<< 自动续领检查完成")
