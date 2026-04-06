"""
VIP 服务层 - VIP状态查询、签到领取、自动续期
对齐 EchoMusic 方案：先领取畅听VIP，再升级概念VIP
"""
from datetime import datetime, timezone, timedelta, date
from loguru import logger
from sqlalchemy import select, update

from app.services.kugou_client import kugou_client
from app.models import Account, ClaimLog

# 中国时区 UTC+8
_CST = timezone(timedelta(hours=8))


def _now():
    """当前中国本地时间"""
    return datetime.now(_CST)


def _fmt_cst(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_CST).strftime("%Y-%m-%d %H:%M:%S")


def _parse_vip_expire_time(value) -> datetime | None:
    """解析 VIP 过期时间，统一转为中国时区"""
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.astimezone(_CST) if value.tzinfo else value.replace(tzinfo=_CST)

    raw = str(value).strip()
    if not raw:
        return None

    # Unix 时间戳 / 毫秒时间戳
    if raw.isdigit():
        if len(raw) == 13:
            return datetime.fromtimestamp(int(raw) / 1000, tz=_CST)
        if len(raw) == 10:
            return datetime.fromtimestamp(int(raw), tz=_CST)

    datetime_formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y%m%d%H%M%S",
    )
    for fmt in datetime_formats:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=_CST)
        except ValueError:
            pass

    # 只有日期时，默认当天 23:59:59 才算过期
    date_formats = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")
    for fmt in date_formats:
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=_CST, hour=23, minute=59, second=59)
        except ValueError:
            pass

    return None


VIP_TYPE_MAP = {
    0: "普通用户",
    1: "VIP会员",
    2: "超级VIP",
    3: "豪华VIP",
}

# busi_vip 中的 product_type 对应中文名
BUSI_VIP_LABELS = {
    "tvip": "畅听VIP",
    "svip": "概念VIP",
}


async def get_vip_status(token: str, userid: str) -> dict:
    """
    查询VIP当前状态
    API 返回 busi_vip 数组，每个元素包含 product_type, is_vip, vip_end_time 等
    """
    try:
        result = await kugou_client.get_vip_info(token=token, userid=userid)
        data = result.get("data") or {}

        # 顶层 vip_type 为主账号 VIP（已购买的），可能过期
        vip_type = int(data.get("vip_type", 0))

        # 解析 busi_vip 数组（签到领取的 VIP 在这里）
        busi_vip_list = data.get("busi_vip") or []
        active_vips = []
        for bv in busi_vip_list:
            product_type = bv.get("product_type", "")
            is_vip = bv.get("is_vip", 0)
            if is_vip != 1:
                continue
            # 只关注签到可领取的类型，排除付费 VIP（qvip/dvip）
            if product_type in ("qvip", "dvip"):
                continue
            end_time_str = bv.get("vip_end_time") or bv.get("auto_pay_time") or ""
            label = BUSI_VIP_LABELS.get(product_type, product_type)
            active_vips.append({
                "product_type": product_type,
                "label": label,
                "vip_end_time": end_time_str,
            })

        # 确定展示的 VIP 类型：优先用 busi_vip 中有效的，否则用顶层 vip_type
        display_type = vip_type
        expire_str = ""
        if active_vips:
            # 如果有有效的概念版VIP，显示最高级的
            svip = next((v for v in active_vips if v["product_type"] == "svip"), None)
            tvip = next((v for v in active_vips if v["product_type"] == "tvip"), None)
            chosen = svip or tvip or active_vips[0]
            expire_str = chosen["vip_end_time"]
            # 概念版用类型 2（超级VIP），畅听用类型 1
            display_type = 2 if chosen["product_type"] == "svip" else 1
        else:
            # 用顶层的过期时间
            top_expire = data.get("vip_end_time") or ""
            if top_expire:
                expire_str = top_expire

        return {
            "success": True,
            "vip_type": display_type,
            "vip_label": VIP_TYPE_MAP.get(display_type, f"类型{display_type}"),
            "expire_time": expire_str,
            "active_vips": active_vips,  # 前端可用来展示多个 VIP 标签
            "raw": data,
        }
    except Exception as e:
        logger.error(f"获取VIP信息失败: {e}")
        return {"success": False, "message": str(e)}


async def get_sign_info(token: str, userid: str) -> dict:
    """
    获取今日签到状态和连续签到信息
    接口：/youth/v1/activity/get_month_vip_record
    返回格式：{"status":1,"data":{"list":[{"date":"20260405","reward":...}, ...]}}
    """
    try:
        result = await kugou_client.get_sign_info(token=token, userid=userid)
        data = result.get("data") or {}

        today_str = _now().date().strftime("%Y-%m-%d")  # 使用中国时区

        # data 可能直接是 list（部分版本），也可能是 {"list": [...]}
        records = []
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            records = data.get("list") or data.get("records") or []

        # 字段可能是 "day"（格式 "2026-01-02"）或 "date"/"sign_date"（格式 "20260102"）
        signed_today = any(
            str(r.get("day") or r.get("date") or r.get("sign_date") or "") == today_str
            or str(r.get("date") or r.get("sign_date") or "").startswith(today_str.replace("-", ""))
            for r in records
        )
        total_days = len(records)

        return {
            "success": True,
            "signed_today": signed_today,
            "continuous_days": total_days,
            "total_days": total_days,
            "reward_desc": "",
            "raw": data,
        }
    except Exception as e:
        logger.error(f"获取签到信息失败: {e}")
        return {"success": False, "message": str(e), "signed_today": False}


async def should_auto_renew(token: str, userid: str) -> dict:
    """
    判断账号当前是否需要自动续领：
    - busi_vip 中仍有有效 VIP => 不续领
    - 顶层 vip_type 且过期时间未到 => 不续领
    - 其余情况 => 允许自动续领
    """
    vip_status = await get_vip_status(token, userid)
    if not vip_status.get("success"):
        return {
            "success": False,
            "should_renew": True,
            "message": vip_status.get("message", "获取VIP状态失败，将继续尝试自动续领"),
            "expire_time": "",
        }

    active_vips = vip_status.get("active_vips") or []
    if active_vips:
        preferred = next((v for v in active_vips if v.get("product_type") == "svip"), None) or active_vips[0]
        return {
            "success": True,
            "should_renew": False,
            "message": "当前VIP仍有效，跳过自动续领",
            "expire_time": preferred.get("vip_end_time", ""),
        }

    expire_time = vip_status.get("expire_time") or ""
    expire_at = _parse_vip_expire_time(expire_time)
    vip_type = int(vip_status.get("vip_type", 0) or 0)

    if vip_type > 0 and expire_at and expire_at > _now():
        return {
            "success": True,
            "should_renew": False,
            "message": "当前VIP仍有效，跳过自动续领",
            "expire_time": expire_time,
        }

    return {
        "success": True,
        "should_renew": True,
        "message": "当前VIP已过期，准备自动续领",
        "expire_time": expire_time,
    }


async def do_sign_in(token: str, userid: str, db) -> dict:
    """
    执行签到/领取VIP操作（对齐 EchoMusic 两步流程）
    第1步：领取畅听VIP (receive_vip_listen_song)
    第2步：升级概念VIP (upgrade_vip_reward)
    """
    try:
        # 先检查今日是否已签到
        sign_info = await get_sign_info(token, userid)
        if sign_info.get("signed_today"):
            _write_claim_log(db, userid, "skipped", "今日已签到", "sign_in")
            return {
                "success": True,
                "skipped": True,
                "message": "今日已完成签到",
                "continuous_days": sign_info.get("continuous_days", 0),
            }

        # 第1步：领取畅听VIP
        tvip_result = await kugou_client.receive_tvip(token=token, userid=userid)
        tvip_error_code = tvip_result.get("error_code", tvip_result.get("errcode", -1))
        tvip_status = tvip_result.get("status", 0)
        tvip_success = tvip_error_code == 0 and tvip_status == 1
        # error_code=30000/30002: 今日已领取过
        tvip_already = tvip_error_code in (30000, 30002)

        if tvip_success:
            tvip_msg = "畅听VIP领取成功"
        elif tvip_already:
            tvip_msg = "畅听VIP今日已领取"
            tvip_success = True  # 视为已领取
        else:
            tvip_err = tvip_result.get("error_msg") or tvip_result.get("errmsg") or tvip_result.get("msg") or f"畅听VIP领取失败(error_code={tvip_error_code})"
            _write_claim_log(db, userid, "failed", tvip_err, "receive_tvip")
            return {"success": False, "message": tvip_err}

        # 第2步：尝试升级概念VIP（需要先有畅听VIP）
        svip_success = False
        svip_msg = ""
        # 先查询当前 VIP 状态，判断是否已有概念VIP
        vip_info = await get_vip_status(token, userid)
        busi_vips = vip_info.get("active_vips", [])
        has_svip = any(v.get("product_type") == "svip" for v in busi_vips)

        if has_svip:
            svip_success = True
            svip_msg = "概念VIP已生效"
        else:
            # 尝试升级
            upgrade_result = await kugou_client.upgrade_svip(token=token, userid=userid)
            upgrade_error_code = upgrade_result.get("error_code", upgrade_result.get("errcode", -1))
            upgrade_status = upgrade_result.get("status", 0)
            # error_code=297002 表示已达上限（也算成功）
            if upgrade_status == 1 and upgrade_error_code == 0:
                svip_success = True
                svip_msg = "概念VIP升级成功"
            elif upgrade_error_code == 297002:
                svip_success = True
                svip_msg = "概念VIP已达上限"
            else:
                upgrade_err = upgrade_result.get("error_msg") or upgrade_result.get("errmsg") or upgrade_result.get("msg") or ""
                svip_msg = f"概念VIP升级失败: {upgrade_err}" if upgrade_err else "概念VIP升级失败"
                # 升级失败不算整体失败，畅听VIP已领取成功

        # 组装结果消息
        if svip_success and "升级成功" in svip_msg:
            full_msg = f"签到成功！{tvip_msg}，{svip_msg}"
        elif svip_success:
            full_msg = f"签到成功！{tvip_msg}，{svip_msg}"
        else:
            full_msg = f"签到成功！{tvip_msg}（{svip_msg}）"

        _write_claim_log(db, userid, "success", full_msg, "sign_in")
        _update_account_claim_time(db, userid)

        return {
            "success": True,
            "skipped": False,
            "message": full_msg,
            "tvip_success": tvip_success,
            "svip_success": svip_success,
            "tvip_msg": tvip_msg,
            "svip_msg": svip_msg,
        }

    except Exception as e:
        err = f"签到请求异常: {str(e)}"
        logger.error(err)
        _write_claim_log(db, userid, "failed", err, "sign_in")
        return {"success": False, "message": err}


def _write_claim_log(db, userid: str, status: str, message: str, claim_type: str):
    """写入领取日志"""
    try:
        log = ClaimLog(userid=userid, status=status, message=message, claim_type=claim_type)
        db.add(log)
        db.commit()
    except Exception as e:
        logger.error(f"写入日志失败: {e}")


def _update_account_claim_time(db, userid: str):
    """更新账号最后领取时间"""
    try:
        db.execute(
            update(Account)
            .where(Account.userid == userid)
            .values(last_claim_time=_now())
        )
        db.commit()
    except Exception as e:
        logger.error(f"更新领取时间失败: {e}")


def get_claim_logs(db, userid: str, limit: int = 20) -> list[dict]:
    """获取最近的领取日志"""
    try:
        result = db.execute(
            select(ClaimLog)
            .where(ClaimLog.userid == userid)
            .order_by(ClaimLog.created_at.desc())
            .limit(limit)
        )
        logs = result.scalars().all()
        return [
            {
                "id": log.id,
                "status": log.status,
                "message": log.message,
                "claim_type": log.claim_type,
                "created_at": _fmt_cst(log.created_at),
            }
            for log in logs
        ]
    except Exception as e:
        logger.error(f"获取日志失败: {e}")
        return []
