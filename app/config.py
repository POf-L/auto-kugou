"""
全局配置模块
"""
import os
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/kugou.db")
TOKEN_REFRESH_INTERVAL = int(os.getenv("TOKEN_REFRESH_INTERVAL", 5400))
VIP_CHECK_INTERVAL = int(os.getenv("VIP_CHECK_INTERVAL", 3600))

# ========== 酷狗 API 常量（来自 KuGouMusicApi/util/config.json） ==========
APP_ID = int(os.getenv("KUGOU_APPID", 1005))          # 酷狗主 appid
CLIENT_VER = int(os.getenv("KUGOU_CLIENTVER", 20489))  # clientver
SRC_APP_ID = int(os.getenv("KUGOU_SRCAPPID", 2919))   # srcappid

# 请求签名 salt（来自 KuGouMusicApi/util/helper.js）
SIGNATURE_ANDROID_SALT = "OIlwieks28dk2k092lksi2UIkp"
SIGNATURE_WEB_SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
SIGN_KEY_SALT = "57ae12eb6890223e355ccfcb74edf70d"      # signKey 用
SIGN_PARAMS_SALT = "R6snCXJgbCaj9WFRJKefTMIFp0ey6Gza"  # signParams 用

# 各接口 Base URL
KUGOU_LOGIN_URL = "https://login.user.kugou.com"           # 密码登录
KUGOU_LOGIN_USER_URL = "https://login-user.kugou.com"      # 二维码/QR
KUGOU_LOGIN_RETRY_URL = "https://loginserviceretry.kugou.com"  # 验证码登录
KUGOU_GATEWAY_URL = "https://gateway.kugou.com"            # 通用网关
KUGOU_YOUTH_URL = "https://gateway.kugou.com"              # 青春版/概念版 API（同网关）
KUGOU_VIP_URL = "https://kugouvip.kugou.com"               # VIP 相关接口

# RSA 公钥（来自 KuGouMusicApi/util/crypto.js）
RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDIAG7QOELSYoIJvTFJhMpe1s/g
bjDJX51HBNnEl5HXqTW6lQ7LC8jr9fWZTwusknp+sVGzwd40MwP6U5yDE27M/X1+
UR4tvOGOqp94TJtQ1EPnWGWXngpeIW5GxoQGao1rmYWAu6oi1z9XkChrsUdC6DJE
5E221wf/4WLFxwAtRQIDAQAB
-----END PUBLIC KEY-----"""

# 模拟 Android 客户端身份
USER_AGENT_ANDROID = "Android16-1070-11440-130-0-LOGIN-wifi"
USER_AGENT_GATEWAY = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"
