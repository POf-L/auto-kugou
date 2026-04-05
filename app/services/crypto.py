"""
酷狗音乐加密模块
完全对齐 KuGouMusicApi/util/crypto.js 和 helper.js 的实现
"""
import os
import json
import hashlib
import hmac
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from Crypto.PublicKey import RSA

from app.config import RSA_PUBLIC_KEY, SIGNATURE_ANDROID_SALT, SIGNATURE_WEB_SALT


# ========== 工具函数 ==========

def random_string(length: int = 16) -> str:
    """
    生成随机大写字母+数字字符串（对应 JS 的 randomString）
    字符集: 1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ
    """
    chars = "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(chars[b % len(chars)] for b in os.urandom(length))


def random_string_lower(length: int = 16) -> str:
    """
    生成随机小写字母+数字字符串（login.js 里用到的 randomString(16).toLowerCase()）
    """
    return random_string(length).lower()


# ========== Hash ==========

def crypto_md5(data) -> str:
    """MD5 加密，返回 32 位小写 hex（对应 JS cryptoMd5）"""
    if isinstance(data, dict):
        data = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    elif not isinstance(data, str):
        data = str(data)
    return hashlib.md5(data.encode("utf-8")).hexdigest()


def crypto_sha1(data) -> str:
    """SHA1 加密，返回小写 hex（对应 JS cryptoSha1）"""
    if isinstance(data, dict):
        data = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    elif not isinstance(data, str):
        data = str(data)
    return hashlib.sha1(data.encode("utf-8")).hexdigest()


# ========== AES ==========

def _derive_key_iv(temp_key: str) -> tuple[str, str]:
    """
    从 temp_key 派生 AES key 和 IV（对应 JS cryptoAesEncrypt 中不传 opt 时的逻辑）
      key = md5(tempKey)[0:32]
      iv  = key[-16:]
    """
    md5 = crypto_md5(temp_key)
    key = md5[:32]
    iv = key[-16:]
    return key, iv


def crypto_aes_encrypt(data, opt: dict = None) -> dict | str:
    """
    AES-256-CBC 加密（完全对应 JS cryptoAesEncrypt）

    - 若 opt 含 key+iv，则直接用，返回 hex 字符串
    - 否则自动生成 temp_key，返回 { str: hex, key: temp_key }
    """
    if isinstance(data, dict):
        data = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    elif not isinstance(data, str):
        data = str(data)

    data_bytes = data.encode("utf-8")

    if opt and opt.get("key") and opt.get("iv"):
        key = opt["key"]
        iv = opt["iv"]
        cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
        encrypted = cipher.encrypt(pad(data_bytes, AES.block_size))
        return encrypted.hex()
    else:
        temp_key = (opt.get("key") if opt else None) or random_string_lower(16)
        key, iv = _derive_key_iv(temp_key)
        cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
        encrypted = cipher.encrypt(pad(data_bytes, AES.block_size))
        return {"str": encrypted.hex(), "key": temp_key}


def crypto_aes_decrypt(hex_data: str, key: str, iv: str = None):
    """
    AES-256-CBC 解密（对应 JS cryptoAesDecrypt）
    - 若 iv 不传，则从 key 派生（key = md5(key)[0:32], iv = key[-16:]）
    - 尝试 JSON parse，失败则返回字符串
    """
    if not iv:
        key = crypto_md5(key)[:32]
    iv = iv or key[-16:]

    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
    decrypted = unpad(cipher.decrypt(bytes.fromhex(hex_data)), AES.block_size)
    text = decrypted.decode("utf-8")
    try:
        return json.loads(text)
    except Exception:
        return text


# ========== RSA ==========

def _load_rsa_key():
    """加载 RSA 公钥（缓存）"""
    if not hasattr(_load_rsa_key, "_cache"):
        _load_rsa_key._cache = RSA.import_key(RSA_PUBLIC_KEY)
    return _load_rsa_key._cache


def crypto_rsa_encrypt(data) -> str:
    """
    RSA 无填充加密（对应 JS cryptoRSAEncrypt / rsaRawEncrypt）
    - 数据右对齐，左补 0x00，然后执行 m^e mod n
    - 返回 128字节(1024bit) hex 字符串（大写）
    """
    if isinstance(data, dict):
        data = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    elif not isinstance(data, str):
        data = str(data)

    data_bytes = data.encode("utf-8")
    rsa_key = _load_rsa_key()
    key_size = (rsa_key.n.bit_length() + 7) // 8  # 通常是 128

    if len(data_bytes) > key_size:
        raise ValueError(f"RSA加密数据超长: {len(data_bytes)} > {key_size}")

    # 右对齐补零（JS: padded = new Uint8Array(keyLength); padded.set(buffer)）
    padded = b"\x00" * (key_size - len(data_bytes)) + data_bytes

    m = int.from_bytes(padded, "big")
    c = pow(m, rsa_key.e, rsa_key.n)
    return c.to_bytes(key_size, "big").hex().upper()


def rsa_encrypt2(data) -> str:
    """
    RSA PKCS1_v1_5 加密（对应 JS rsaEncrypt2，用于 register_dev）
    返回 hex 字符串
    """
    from Crypto.Cipher import PKCS1_v1_5 as PKCS1_cipher
    if isinstance(data, dict):
        data = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    elif not isinstance(data, str):
        data = str(data)

    rsa_key = _load_rsa_key()
    cipher = PKCS1_cipher.new(rsa_key)
    encrypted = cipher.encrypt(data.encode("utf-8"))
    return encrypted.hex()


# ========== Signature ==========

def signature_android_params(params: dict, data: str = "") -> str:
    """
    Android 请求签名（对应 JS signatureAndroidParams）
    salt = 'OIlwieks28dk2k092lksi2UIkp'
    sig = md5( salt + sorted(key=val pairs) + data + salt )
    """
    salt = SIGNATURE_ANDROID_SALT
    parts = sorted(
        f"{k}={json.dumps(v, separators=(',', ':'), ensure_ascii=False) if isinstance(v, (dict, list)) else v}"
        for k, v in params.items()
    )
    params_str = "".join(parts)
    return crypto_md5(f"{salt}{params_str}{data or ''}{salt}")


def signature_web_params(params: dict) -> str:
    """
    Web 请求签名（对应 JS signatureWebParams）
    salt = 'NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt'
    sig = md5( salt + sorted(key=val) + salt )
    """
    salt = SIGNATURE_WEB_SALT
    parts = sorted(f"{k}={v}" for k, v in params.items())
    params_str = "".join(parts)
    return crypto_md5(f"{salt}{params_str}{salt}")


def sign_params_key(clienttime_ms: int, appid: int = None, clientver: int = None) -> str:
    """
    signParamsKey（对应 JS signParamsKey）
    = md5( appid + salt + clientver + clienttime_ms )
    """
    from app.config import APP_ID, CLIENT_VER, SIGNATURE_ANDROID_SALT
    salt = SIGNATURE_ANDROID_SALT
    _appid = appid or APP_ID
    _clientver = clientver or CLIENT_VER
    return crypto_md5(f"{_appid}{salt}{_clientver}{clienttime_ms}")


# ========== 登录专用加密 ==========

def encrypt_login_password(password: str, timestamp_ms: int) -> dict:
    """
    密码登录加密（对应 JS login.js）
    返回 { params: AES加密后hex, pk: RSA加密后hex(大写), key: temp_key }
    """
    # AES 加密 payload
    encrypt_result = crypto_aes_encrypt({"pwd": password, "code": "", "clienttime_ms": timestamp_ms})
    # RSA 加密 AES key
    pk = crypto_rsa_encrypt({"clienttime_ms": timestamp_ms, "key": encrypt_result["key"]})
    return {
        "params": encrypt_result["str"],
        "pk": pk.upper(),
        "key": encrypt_result["key"],
    }


def encrypt_sms_payload(mobile: str, code: str, timestamp_ms: int) -> dict:
    """
    手机验证码登录加密（对应 JS login_cellphone.js）
    返回 { params: AES加密后hex, pk: RSA加密后hex, key: temp_key }
    """
    encrypt_result = crypto_aes_encrypt({"mobile": mobile, "code": code})
    pk = crypto_rsa_encrypt({"clienttime_ms": timestamp_ms, "key": encrypt_result["key"]})
    return {
        "params": encrypt_result["str"],
        "pk": pk.upper(),
        "key": encrypt_result["key"],
    }
