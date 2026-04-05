"""
全局配置模块
"""
import os
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8000))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# 数据库: 优先使用 Turso (libSQL)，回退到本地 SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")
if not DATABASE_URL:
    # 本地 SQLite 默认路径
    DATABASE_URL = "sqlite:///./data/kugou.db"

TOKEN_REFRESH_INTERVAL = int(os.getenv("TOKEN_REFRESH_INTERVAL", 5400))
VIP_CHECK_INTERVAL = int(os.getenv("VIP_CHECK_INTERVAL", 3600))

# ========== 酷狗 API 常量 ==========
APP_ID = int(os.getenv("KUGOU_APPID", 1005))
CLIENT_VER = int(os.getenv("KUGOU_CLIENTVER", 20489))
SRC_APP_ID = int(os.getenv("KUGOU_SRCAPPID", 2919))

# 签名 salt
SIGNATURE_ANDROID_SALT = "OIlwieks28dk2k092lksi2UIkp"
SIGNATURE_WEB_SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"
SIGN_KEY_SALT = "57ae12eb6890223e355ccfcb74edf70d"
SIGN_PARAMS_SALT = "R6snCXJgbCaj9WFRJKefTMIFp0ey6Gza"

# API URLs
KUGOU_LOGIN_URL = "https://login.user.kugou.com"
KUGOU_LOGIN_USER_URL = "https://login-user.kugou.com"
KUGOU_LOGIN_RETRY_URL = "https://loginserviceretry.kugou.com"
KUGOU_GATEWAY_URL = "https://gateway.kugou.com"
KUGOU_YOUTH_URL = "https://gateway.kugou.com"
KUGOU_VIP_URL = "https://kugouvip.kugou.com"

# RSA 公钥
RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDIAG7QOELSYoIJvTFJhMpe1s/g
bjDJX51HBNnEl5HXqTW6lQ7LC8jr9fWZTwusknp+sVGzwd40MwP6U5yDE27M/X1+
UR4tvOGOqp94TJtQ1EPnWGWXngpeIW5GxoQGao1rmYWAu6oi1z9XkChrsUdC6DJE
5E221wf/4WLFxwAtRQIDAQAB
-----END PUBLIC KEY-----"""

USER_AGENT_ANDROID = "Android16-1070-11440-130-0-LOGIN-wifi"
USER_AGENT_GATEWAY = "Android15-1070-11083-46-0-DiscoveryDRADProtocol-wifi"

# ========== Vercel Cron 密钥 ==========
CRON_SECRET = os.getenv("CRON_SECRET", "kugou_cron_secret_change_me")

# ========== JWT 密钥 ==========
JWT_SECRET = os.getenv("JWT_SECRET", "kugou_jwt_secret_change_me")
JWT_ALGORITHM = "HS256"
