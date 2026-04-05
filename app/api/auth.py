"""
认证相关 API 路由
"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from datetime import datetime, timezone, timedelta

from app.models import get_db, Account
from app.services import auth_service
from app.services import vip_service
from app.tasks.scheduler import emit_event


router = APIRouter(prefix="/api/auth", tags=["auth"])

# 存储二维码轮询的会话
_qr_sessions: dict[str, str] = {}  # key -> userid(若已登录)


class PasswordLoginRequest(BaseModel):
    username: str
    password: str


class SmsCodeRequest(BaseModel):
    mobile: str


class SmsLoginRequest(BaseModel):
    mobile: str
    code: str


class QrCheckRequest(BaseModel):
    key: str


@router.post("/login/password")
async def login_password(req: PasswordLoginRequest, db: AsyncSession = Depends(get_db)):
    """账号密码登录"""
    result = await auth_service.login_by_password(req.username, req.password)
    if result.get("success"):
        await _save_account(db, result, "password")
        emit_event("login_success", result["userid"], f"账号 {result.get('nickname', result['userid'])} 登录成功（密码）")
    return result


@router.post("/login/sms/send")
async def send_sms(req: SmsCodeRequest):
    """发送手机验证码"""
    return await auth_service.send_sms_code(req.mobile)


@router.post("/login/sms")
async def login_sms(req: SmsLoginRequest, db: AsyncSession = Depends(get_db)):
    """手机验证码登录"""
    result = await auth_service.login_by_sms(req.mobile, req.code)
    if result.get("success"):
        await _save_account(db, result, "sms")
        emit_event("login_success", result["userid"], f"账号 {result.get('nickname', result['userid'])} 登录成功（验证码）")
    return result


@router.get("/login/qrcode")
async def get_qrcode():
    """获取登录二维码"""
    return await auth_service.create_qrcode()


@router.post("/login/qrcode/check")
async def check_qrcode(req: QrCheckRequest, db: AsyncSession = Depends(get_db)):
    """检查二维码登录状态"""
    result = await auth_service.check_qrcode_status(req.key)
    if result.get("qr_status") == "success" and result.get("success"):
        await _save_account(db, result, "qrcode")
        emit_event("login_success", result["userid"], f"账号 {result.get('nickname', result['userid'])} 登录成功（扫码）")
    return result


@router.get("/accounts")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    """获取所有已登录账号（实时查询VIP状态）"""
    result = await db.execute(select(Account).order_by(Account.created_at.desc()))
    accounts = result.scalars().all()

    # 并行查询所有账号的实时VIP状态
    async def fetch_vip(acc: Account) -> dict:
        try:
            if acc.token:
                vip = await vip_service.get_vip_status(acc.token, acc.userid)
                if vip.get("success"):
                    # 同步更新数据库中的 vip_type
                    new_type = vip.get("vip_type", acc.vip_type)
                    expire_str = vip.get("expire_time", "")
                    if new_type != acc.vip_type:
                        acc.vip_type = new_type
                        # 更新过期时间
                        if expire_str:
                            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d%H%M%S", "%Y%m%d"):
                                try:
                                    acc.vip_expire_time = datetime.strptime(expire_str, fmt)
                                    break
                                except ValueError:
                                    continue
                    return {**_format_account(acc), "active_vips": vip.get("active_vips", []), "vip_expire": vip.get("expire_time", "")}
            return {**_format_account(acc), "active_vips": [], "vip_expire": ""}
        except Exception:
            return {**_format_account(acc), "active_vips": [], "vip_expire": ""}

    accounts_data = await asyncio.gather(*[fetch_vip(acc) for acc in accounts])
    await db.commit()
    return accounts_data


@router.delete("/accounts/{userid}")
async def remove_account(userid: str, db: AsyncSession = Depends(get_db)):
    """移除账号"""
    result = await db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    await db.delete(acc)
    await db.commit()
    return {"success": True, "message": "账号已移除"}


@router.post("/accounts/{userid}/toggle_auto_claim")
async def toggle_auto_claim(userid: str, db: AsyncSession = Depends(get_db)):
    """切换自动领取开关"""
    result = await db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账号不存在")
    new_val = not acc.auto_claim
    await db.execute(
        update(Account).where(Account.userid == userid).values(auto_claim=new_val)
    )
    await db.commit()
    return {"success": True, "auto_claim": new_val}


async def _save_account(db: AsyncSession, login_result: dict, login_type: str):
    """保存或更新账号信息"""
    userid = login_result.get("userid", "")
    if not userid:
        return
    result = await db.execute(select(Account).where(Account.userid == userid))
    acc = result.scalar_one_or_none()

    if acc:
        acc.token = login_result.get("token", acc.token)
        acc.vip_type = login_result.get("vip_type", acc.vip_type)
        acc.nickname = login_result.get("nickname", acc.nickname) or acc.nickname
        acc.avatar = login_result.get("avatar", acc.avatar) or acc.avatar
        acc.mobile = login_result.get("mobile", acc.mobile) or acc.mobile
        acc.login_type = login_type
        acc.last_token_refresh = datetime.now(timezone(timedelta(hours=8)))
        acc.is_active = True
    else:
        acc = Account(
            userid=userid,
            token=login_result.get("token", ""),
            vip_type=login_result.get("vip_type", 0),
            nickname=login_result.get("nickname", ""),
            avatar=login_result.get("avatar", ""),
            mobile=login_result.get("mobile", ""),
            login_type=login_type,
            last_token_refresh=datetime.now(timezone(timedelta(hours=8))),
        )
        db.add(acc)
    await db.commit()


def _format_account(acc: Account) -> dict:
    return {
        "userid": acc.userid,
        "nickname": acc.nickname,
        "avatar": acc.avatar,
        "mobile": acc.mobile,
        "vip_type": acc.vip_type,
        "login_type": acc.login_type,
        "auto_claim": acc.auto_claim,
        "is_active": acc.is_active,
        "last_claim_time": acc.last_claim_time.strftime("%Y-%m-%d %H:%M:%S") if acc.last_claim_time else "",
        "last_token_refresh": acc.last_token_refresh.strftime("%Y-%m-%d %H:%M:%S") if acc.last_token_refresh else "",
        "created_at": acc.created_at.strftime("%Y-%m-%d %H:%M:%S") if acc.created_at else "",
    }
