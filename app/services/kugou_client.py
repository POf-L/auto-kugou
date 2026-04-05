"""
酷狗音乐 API 客户端
完全对齐 KuGouMusicApi/util/request.js 的请求构造逻辑
"""
import time
import math
import json
import aiohttp
from loguru import logger

from app.config import (
    APP_ID, CLIENT_VER, SRC_APP_ID,
    KUGOU_LOGIN_URL, KUGOU_LOGIN_USER_URL,
    KUGOU_LOGIN_RETRY_URL, KUGOU_GATEWAY_URL,
    KUGOU_YOUTH_URL, KUGOU_VIP_URL,
    USER_AGENT_ANDROID, USER_AGENT_GATEWAY,
)
from app.services.crypto import (
    signature_android_params,
    signature_web_params,
    crypto_md5,
)


# 固定的 MID（模拟设备，对应 JS 里的 KUGOU_API_MID cookie）
_DEFAULT_MID = "0f607264fc6318a92b9e13c65db7cd3c"
_DEFAULT_DFID = "-"


def _build_default_params(token: str = "", userid=0) -> dict:
    """
    构造所有请求通用的基础参数（对应 JS createRequest 里的 defaultParams）
    """
    clienttime = math.floor(time.time())
    params = {
        "dfid": _DEFAULT_DFID,
        "mid": _DEFAULT_MID,
        "uuid": "-",
        "appid": APP_ID,
        "clientver": CLIENT_VER,
        "clienttime": clienttime,
    }
    if token:
        params["token"] = token
    if userid and int(userid) != 0:
        params["userid"] = int(userid)
    return params


def _build_headers(dfid=_DEFAULT_DFID, mid=_DEFAULT_MID, clienttime=None,
                   user_agent=None, extra: dict = None) -> dict:
    """
    构造通用请求头（对应 JS createRequest headers 部分）
    """
    if clienttime is None:
        clienttime = math.floor(time.time())
    headers = {
        "User-Agent": user_agent or USER_AGENT_GATEWAY,
        "dfid": dfid,
        "clienttime": str(clienttime),
        "mid": mid,
        "kg-rc": "1",
        "kg-thash": "5d816a0",
        "kg-rec": "1",
        "kg-rf": "B9EDA08A64250DEFFBCADDEE00F8F25F",
    }
    if extra:
        headers.update(extra)
    return headers


def _sign_android(params: dict, data_str: str = "") -> dict:
    """为 params 添加 android signature"""
    p = dict(params)
    p["signature"] = signature_android_params(p, data_str)
    return p


def _sign_web(params: dict) -> dict:
    """为 params 添加 web signature"""
    p = dict(params)
    p["signature"] = signature_web_params(p)
    return p


class KugouClient:
    """酷狗 API 异步客户端（完全对齐 KuGouMusicApi 请求逻辑）"""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=False)
            )
        return self._session

    async def _request(self, method: str, url: str, *,
                       params: dict = None, data=None,
                       headers: dict = None) -> dict:
        """底层 HTTP 请求（GET/POST）"""
        session = await self._get_session()
        try:
            kwargs = dict(
                headers=headers or {},
                timeout=aiohttp.ClientTimeout(total=15),
            )
            if params:
                kwargs["params"] = params
            if data is not None:
                kwargs["data"] = data

            async with session.request(method, url, **kwargs) as resp:
                text = await resp.text()
                logger.debug(f"{method} {url} => {resp.status}, len={len(text)}, body={text[:500]}")
                if not text:
                    raise ValueError(f"Empty response from {url}")
                try:
                    result = await resp.json(content_type=None)
                    return result
                except Exception:
                    # 尝试提取 JSON
                    start = text.find("{")
                    end = text.rfind("}") + 1
                    if start >= 0 and end > start:
                        try:
                            return json.loads(text[start:end])
                        except Exception:
                            pass
                    logger.warning(f"JSON 解析失败 {url}: {text[:200]}")
                    return {"status": 0, "errcode": -1,
                            "error_msg": f"响应解析失败: {text[:100]}"}
        except Exception as e:
            logger.error(f"请求失败 {url}: {e}")
            raise

    async def _android_post(self, base_url: str, path: str, body: dict,
                            token: str = "", userid=0,
                            extra_headers: dict = None,
                            user_agent: str = None) -> dict:
        """
        构造 Android 类型的 POST 请求（带 signature）
        对应 JS: encryptType='android', method='POST'
        """
        params = _build_default_params(token=token, userid=userid)
        data_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body else ""
        params = _sign_android(params, data_str)

        headers = _build_headers(
            clienttime=params["clienttime"],
            user_agent=user_agent or USER_AGENT_ANDROID,
            extra=extra_headers,
        )
        headers["Content-Type"] = "application/json"

        return await self._request("POST", base_url + path,
                                   params=params, data=data_str, headers=headers)

    async def _android_form_post(self, base_url: str, path: str, form: dict,
                                 token: str = "", userid=0,
                                 extra_headers: dict = None,
                                 user_agent: str = None) -> dict:
        """
        构造 Android 类型的 表单 POST 请求（带 signature）
        部分登录接口用 form-data 而非 JSON
        """
        params = _build_default_params(token=token, userid=userid)
        # form 接口不在 data 里算签名
        params = _sign_android(params)

        headers = _build_headers(
            clienttime=params["clienttime"],
            user_agent=user_agent or USER_AGENT_ANDROID,
            extra=extra_headers,
        )
        headers["Content-Type"] = "application/x-www-form-urlencoded"

        # 把 form body 和 params 合并发送（有些登录接口把参数放 body）
        merged_form = {**form}

        return await self._request("POST", base_url + path,
                                   params=params, data=merged_form, headers=headers)

    async def _web_get(self, base_url: str, path: str,
                       extra_params: dict = None, token: str = "", userid=0) -> dict:
        """
        构造 Web 类型的 GET 请求（带 web signature）
        对应 JS: encryptType='web', method='GET'
        """
        params = _build_default_params(token=token, userid=userid)
        if extra_params:
            params.update(extra_params)
        params = _sign_web(params)

        headers = _build_headers(
            clienttime=params["clienttime"],
            user_agent=USER_AGENT_GATEWAY,
        )
        return await self._request("GET", base_url + path,
                                   params=params, headers=headers)

    async def _android_get(self, base_url: str, path: str,
                           extra_params: dict = None, token: str = "", userid=0) -> dict:
        """
        构造 Android 类型的 GET 请求（带 android signature）
        对应 JS: encryptType='android', method='GET'
        """
        params = _build_default_params(token=token, userid=userid)
        if extra_params:
            params.update(extra_params)
        params = _sign_android(params)

        headers = _build_headers(
            clienttime=params["clienttime"],
            user_agent=USER_AGENT_GATEWAY,
        )
        return await self._request("GET", base_url + path,
                                   params=params, headers=headers)

    # ========== 登录相关 ==========

    async def send_sms_code(self, mobile: str) -> dict:
        """
        发送手机验证码
        对应 JS: captcha_sent.js
        POST http://login.user.kugou.com/v7/send_mobile_code
        """
        form = {
            "businessid": "5",
            "mobile": str(mobile),
            "plat": "3",
        }
        return await self._android_form_post(
            KUGOU_LOGIN_URL, "/v7/send_mobile_code",
            form=form,
            user_agent=USER_AGENT_ANDROID,
        )

    async def login_by_password(self, username: str, params_encrypted: str,
                                pk: str, timestamp_ms: int,
                                token: str = "", userid=0) -> dict:
        """
        账号密码登录
        对应 JS: login.js => POST /v9/login_by_pwd
        - params: AES加密的 {pwd, code, clienttime_ms}
        - pk:     RSA加密的 {clienttime_ms, key}
        """
        form = {
            "plat": "1",
            "support_multi": "1",
            "clienttime_ms": str(timestamp_ms),
            # 固定值（来自 login.js 源码中的 t1/t2/t3 hardcode）
            "t1": "562a6f12a6e803453647d16a08f5f0c2ff7eee692cba2ab74cc4c8ab47fc467561a7c6b586ce7dc46a63613b246737c03a1dc8f8d162d8ce1d2c71893d19f1d4b797685a4c6d3d81341cbde65e488c4829a9b4d42ef2df470eb102979fa5adcdd9b4eecfea8b909ff7599abeb49867640f10c3c70fc444effca9d15db44a9a6c907731e2bb0f22cd9b3536380169995693e5f0e2424e3378097d3813186e3fe96bbe7023808a0981b4e2b6135a76faac",
            "t2": "31c4daf4cf480169ccea1cb7d4a209295865a9d2b788510301694db229b87807469ea0d41b4d4b9173c2151da7294aeebfc9738df154bbdf11a4e117bb5dff6a3af8ce5ce333e681c1f29a44038f27567d58992eb81283e080778ac77db1400fdf49b7cf7e26be2e5af4da7830cc3be4",
            "t3": "MCwwLDAsMCwwLDAsMCwwLDA=",
            "username": username,
            "params": params_encrypted,
            "pk": pk,
        }
        return await self._android_form_post(
            KUGOU_LOGIN_URL, "/v9/login_by_pwd",
            form=form,
            extra_headers={"x-router": "login.user.kugou.com"},
            user_agent=USER_AGENT_ANDROID,
        )

    async def login_by_sms(self, mobile: str, params_encrypted: str,
                           pk: str, timestamp_ms: int) -> dict:
        """
        手机验证码登录
        对应 JS: login_cellphone.js
        POST https://loginserviceretry.kugou.com/v7/login_by_verifycode
        """
        # 脱敏手机号（JS: mobile.substring(0,2) + '*****' + mobile.substring(10,11)）
        mobile_str = str(mobile)
        mobile_display = f"{mobile_str[:2]}*****{mobile_str[10:11]}" if len(mobile_str) == 11 else mobile_str

        form = {
            "plat": "1",
            "support_multi": "1",
            "t3": "MCwwLDAsMCwwLDAsMCwwLDA=",
            "clienttime_ms": str(timestamp_ms),
            "mobile": mobile_display,
            "params": params_encrypted,
            "pk": pk,
        }
        return await self._android_form_post(
            KUGOU_LOGIN_RETRY_URL, "/v7/login_by_verifycode",
            form=form,
            extra_headers={"support-calm": "1"},
            user_agent=USER_AGENT_ANDROID,
        )

    async def get_qrcode_key(self, qr_type: str = "app") -> dict:
        """
        获取二维码登录的 key
        对应 JS: login_qr_key.js
        GET https://login-user.kugou.com/v2/qrcode
        - appid = 1001（app）或 1014（web）
        """
        appid = 1014 if qr_type == "web" else 1001
        extra = {
            "appid": appid,
            "type": 1,
            "plat": 4,
            "qrcode_txt": f"https://h5.kugou.com/apps/loginQRCode/html/index.html?appid={appid}&",
            "srcappid": SRC_APP_ID,
        }
        return await self._web_get(KUGOU_LOGIN_USER_URL, "/v2/qrcode", extra_params=extra)

    async def check_qrcode(self, qrcode_key: str) -> dict:
        """
        检查二维码扫描状态
        对应 JS: login_qr_check.js
        GET https://login-user.kugou.com/v2/get_userinfo_qrcode
        状态码: 0=过期, 1=等待扫码, 2=待确认, 4=已授权
        """
        extra = {
            "plat": 4,
            "appid": APP_ID,
            "srcappid": SRC_APP_ID,
            "qrcode": qrcode_key,
        }
        return await self._web_get(KUGOU_LOGIN_USER_URL, "/v2/get_userinfo_qrcode",
                                   extra_params=extra)

    async def login_by_token(self, token: str, userid: str) -> dict:
        """
        Token 登录/刷新
        对应 JS: login_token.js
        """
        form = {
            "token": token,
            "userid": userid,
            "plat": "1",
            "support_multi": "1",
        }
        return await self._android_form_post(
            KUGOU_LOGIN_URL, "/v5/login_by_token",
            form=form,
            token=token, userid=userid,
            extra_headers={"x-router": "login.user.kugou.com"},
        )

    async def get_user_info(self, token: str, userid: str) -> dict:
        """获取用户信息"""
        extra = {"token": token, "userid": userid}
        return await self._web_get(KUGOU_GATEWAY_URL, "/v1/user_info",
                                   extra_params=extra, token=token, userid=userid)

    # ========== VIP / 签到相关 ==========

    async def get_vip_info(self, token: str, userid: str) -> dict:
        """
        查询VIP状态
        对应 youth_union_vip.js: GET https://kugouvip.kugou.com/v1/get_union_vip
        encryptType='android'
        参数对齐 EchoMusic: busi_type=concept, opt_product_types=dvip,qvip, product_type=svip
        """
        extra = {
            "busi_type": "concept",
            "opt_product_types": "dvip,qvip",
            "product_type": "svip",
        }
        return await self._android_get(KUGOU_VIP_URL, "/v1/get_union_vip",
                                      extra_params=extra, token=token, userid=userid)

    async def get_sign_info(self, token: str, userid: str) -> dict:
        """
        获取当月签到记录（用来判断今日是否已签到）
        对应 youth_month_vip_record.js:
        GET /youth/v1/activity/get_month_vip_record
        encryptType='android'
        """
        extra = {"latest_limit": 100}
        return await self._android_get(KUGOU_YOUTH_URL, "/youth/v1/activity/get_month_vip_record",
                                      extra_params=extra, token=token, userid=userid)

    async def sign_in(self, token: str, userid: str) -> dict:
        """
        执行签到领取VIP（广告播放上报）
        对应 youth_vip.js: POST /youth/v1/ad/play_report
        ad_id=12307537187, play_start~play_end 模拟30秒广告
        """
        import time as _time
        now_ms = int(_time.time() * 1000)
        body = {
            "ad_id": 12307537187,
            "play_end": now_ms,
            "play_start": now_ms - 30000,
        }
        return await self._android_post(
            KUGOU_YOUTH_URL, "/youth/v1/ad/play_report",
            body=body, token=token, userid=userid,
        )

    async def sign_in_listen_song(self, token: str, userid: str,
                                  mixsongid: int = 666075191) -> dict:
        """
        听歌领取VIP（每日一次）
        对应 youth_listen_song.js: POST /youth/v2/report/listen_song
        """
        body = {"mixsongid": mixsongid}
        # 听歌接口使用特殊 clientver=10566 和独立 UA
        listen_ua = "Android13-1070-10566-201-0-ReportPlaySongToServerProtocol-wifi"
        extra_params_override = {"clientver": 10566}

        import math as _math
        import time as _time
        params = _build_default_params(token=token, userid=userid)
        params.update(extra_params_override)
        data_str = __import__("json").dumps(body, separators=(",", ":"), ensure_ascii=False)
        params = _sign_android(params, data_str)

        headers = _build_headers(
            clienttime=params["clienttime"],
            user_agent=listen_ua,
        )
        headers["Content-Type"] = "application/json; charset=utf-8"

        return await self._request(
            "POST", KUGOU_YOUTH_URL + "/youth/v2/report/listen_song",
            params=params, data=data_str, headers=headers,
        )

    async def receive_tvip(self, token: str, userid: str, receive_day: str = "") -> dict:
        """
        领取畅听VIP（一天）
        对应 youth_day_vip.js: POST /youth/v1/recharge/receive_vip_listen_song
        参数: source_id=90139, receive_day=YYYY-MM-DD
        """
        if not receive_day:
            from datetime import datetime, timezone, timedelta
            cst = timezone(timedelta(hours=8))
            receive_day = datetime.now(cst).strftime("%Y-%m-%d")
        params_extra = {"source_id": 90139, "receive_day": receive_day}
        return await self._android_post(
            KUGOU_YOUTH_URL, "/youth/v1/recharge/receive_vip_listen_song",
            body=params_extra, token=token, userid=userid,
        )

    async def upgrade_svip(self, token: str, userid: str) -> dict:
        """
        升级概念VIP
        对应 youth_day_vip_upgrade.js: POST /youth/v1/listen_song/upgrade_vip_reward
        参数: kugouid=用户ID, ad_type=1
        前置条件: 今日已领取畅听VIP
        """
        body = {
            "kugouid": int(userid),
            "ad_type": "1",
        }
        return await self._android_post(
            KUGOU_YOUTH_URL, "/youth/v1/listen_song/upgrade_vip_reward",
            body=body, token=token, userid=userid,
        )

    async def close(self):
        """关闭 HTTP 会话"""
        if self._session and not self._session.closed:
            await self._session.close()


# 全局单例
kugou_client = KugouClient()
