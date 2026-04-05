"""
认证服务层 - 封装登录业务逻辑
完全对齐 KuGouMusicApi 的登录流程和参数结构
"""
import time
import base64
import io
from loguru import logger

import qrcode

from app.services.crypto import (
    encrypt_login_password,
    encrypt_sms_payload,
    crypto_aes_decrypt,
)
from app.services.kugou_client import kugou_client


def _parse_login_result(result: dict) -> dict:
    """
    解析登录返回结果，提取统一格式的用户信息
    对齐 KuGouMusicApi login.js 中的 secu_params 解密逻辑
    """
    status = result.get("status", 0)
    if status != 1:
        msg = (result.get("error_msg") or result.get("errmsg")
               or result.get("msg") or "登录失败")
        return {"success": False, "message": msg}

    data = result.get("data") or {}
    return {
        "success": True,
        "userid": str(data.get("userid", "")),
        "token": data.get("token", ""),
        "vip_type": int(data.get("vip_type", 0)),
        "vip_token": data.get("vip_token", ""),
        "nickname": data.get("nickname", ""),
        "avatar": data.get("pic", ""),
        "mobile": data.get("mobile", ""),
        "message": "登录成功",
    }


def _decrypt_secu_params(result: dict, temp_key: str) -> dict:
    """
    解密 secu_params 字段（对应 JS login.js 的 cryptoAesDecrypt 步骤）
    酷狗服务端用 AES 加密了 token 等敏感字段，需要用请求时生成的 temp_key 解密
    """
    data = result.get("data") or {}
    secu = data.get("secu_params")
    if not secu:
        return result

    try:
        decrypted = crypto_aes_decrypt(secu, temp_key)
        if isinstance(decrypted, dict):
            result["data"] = {**data, **decrypted}
        else:
            result["data"]["token"] = decrypted
    except Exception as e:
        logger.warning(f"secu_params 解密失败: {e}")

    return result


async def login_by_password(username: str, password: str) -> dict:
    """
    账号密码登录
    支持手机号、用户名、邮箱等账号形式
    """
    timestamp_ms = int(time.time() * 1000)
    try:
        enc = encrypt_login_password(password, timestamp_ms)
        result = await kugou_client.login_by_password(
            username=username,
            params_encrypted=enc["params"],
            pk=enc["pk"],
            timestamp_ms=timestamp_ms,
        )
        if result.get("status") == 1:
            result = _decrypt_secu_params(result, enc["key"])
        return _parse_login_result(result)
    except Exception as e:
        logger.error(f"密码登录失败: {e}")
        return {"success": False, "message": f"请求异常: {str(e)}"}


async def send_sms_code(mobile: str) -> dict:
    """发送手机验证码"""
    try:
        result = await kugou_client.send_sms_code(mobile)
        if result.get("status") == 1:
            return {"success": True, "message": "验证码已发送"}
        msg = (result.get("error_msg") or result.get("errmsg")
               or result.get("msg") or "发送失败，请稍后重试")
        return {"success": False, "message": msg}
    except Exception as e:
        logger.error(f"发送验证码失败: {e}")
        return {"success": False, "message": f"请求异常: {str(e)}"}


async def login_by_sms(mobile: str, code: str) -> dict:
    """手机验证码登录"""
    timestamp_ms = int(time.time() * 1000)
    try:
        enc = encrypt_sms_payload(mobile, code, timestamp_ms)
        result = await kugou_client.login_by_sms(
            mobile=mobile,
            params_encrypted=enc["params"],
            pk=enc["pk"],
            timestamp_ms=timestamp_ms,
        )
        if result.get("status") == 1:
            result = _decrypt_secu_params(result, enc["key"])
        return _parse_login_result(result)
    except Exception as e:
        logger.error(f"验证码登录失败: {e}")
        return {"success": False, "message": f"请求异常: {str(e)}"}


async def create_qrcode(qr_type: str = "app") -> dict:
    """
    生成登录二维码
    返回 base64 编码的二维码图片和用于轮询的 key

    对应 JS: login_qr_key.js + login_qr_create.js
    二维码 URL 格式: https://h5.kugou.com/apps/loginQRCode/html/index.html?qrcode={key}
    """
    try:
        result = await kugou_client.get_qrcode_key(qr_type=qr_type)
        if result.get("status") != 1:
            msg = result.get("error_msg") or result.get("msg") or "获取二维码失败"
            return {"success": False, "message": msg}

        data = result.get("data", {})
        # 服务端返回字段为 "qrcode"（非 "key"），直接包含了 qrcode_img
        qr_key = data.get("qrcode") or data.get("key", "")
        if not qr_key:
            return {"success": False, "message": "二维码 key 获取失败"}

        # 对应 JS login_qr_create.js 的 URL 格式
        qr_url = f"https://h5.kugou.com/apps/loginQRCode/html/index.html?qrcode={qr_key}"

        # 优先使用服务端返回的二维码图片，否则自己生成
        server_img = data.get("qrcode_img", "")
        if server_img:
            qrcode_image = server_img  # 已经是 data:image/png;base64,... 格式
        else:
            qr = qrcode.QRCode(version=1, box_size=8, border=2)
            qr.add_data(qr_url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            qrcode_image = f"data:image/png;base64,{base64.b64encode(buf.read()).decode()}"

        return {
            "success": True,
            "key": qr_key,
            "qrcode_url": qr_url,
            "qrcode_image": qrcode_image,
            "message": "二维码已生成，请用酷狗 APP 扫描",
        }
    except Exception as e:
        logger.error(f"生成二维码失败: {e}")
        return {"success": False, "message": f"请求异常: {str(e)}"}


async def check_qrcode_status(key: str) -> dict:
    """
    检查二维码扫描状态
    对应 JS: login_qr_check.js
    状态码: 0=过期, 1=等待扫码, 2=待确认, 4=已授权登录成功
    """
    try:
        result = await kugou_client.check_qrcode(key)
        # 服务端格式: {"status": 1, "data": {"status": 0/1/2/4}}
        # data.status: 0=过期, 1=等待扫码, 2=待确认, 4=已授权登录成功
        outer_status = result.get("status", 0)
        data = result.get("data") or {}
        login_status = data.get("status", 0) if isinstance(data, dict) else 0

        # 接口本身失败
        if outer_status != 1:
            return {"success": False, "qr_status": "error",
                    "message": result.get("error_msg") or "检查失败"}

        if login_status == 4:
            # 登录成功，data 里有 token/userid
            parsed = _parse_login_result(result)
            parsed["qr_status"] = "success"
            return parsed
        elif login_status == 0:
            return {"success": False, "qr_status": "expired",
                    "message": "二维码已过期，请重新获取"}
        elif login_status == 2:
            return {"success": False, "qr_status": "scanned",
                    "message": "已扫描，等待确认"}
        else:
            return {"success": False, "qr_status": "waiting",
                    "message": "等待扫描..."}
    except Exception as e:
        logger.error(f"检查二维码状态失败: {e}")
        return {"success": False, "qr_status": "error",
                "message": f"请求异常: {str(e)}"}


async def refresh_token(token: str, userid: str) -> dict:
    """Token 续期"""
    try:
        result = await kugou_client.login_by_token(token=token, userid=userid)
        if result.get("status") == 1:
            result = _decrypt_secu_params(result, "")  # token 刷新不需要 secu 解密
        return _parse_login_result(result)
    except Exception as e:
        logger.error(f"Token 刷新失败 userid={userid}: {e}")
        return {"success": False, "message": f"刷新异常: {str(e)}"}
