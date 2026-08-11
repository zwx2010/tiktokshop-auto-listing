"""飞书回调签名校验 + 加密载荷 AES 解密。

飞书签名方案(文档):
- 旧版事件订阅 / 卡片回调: 请求头 `X-Lark-Signature` =
      sha1(timestamp + nonce + encrypt_key)   (Encrypt Key 未启用则 encrypt_key 为空)
- 事件订阅还有 verification_token 兜底: body.token == verification_token
- Encrypt Key 启用时, body 为 {"encrypt": base64}; 解密后才是业务 JSON

真实对接时以你建应用时飞书后台给的 Encrypt Key/Verification Token 为准,
这里实现并单测自洽;路由层允许 env ALLOW_UNVERIFIED=1 本地调试跳过。
"""
import base64
import hashlib

_PAD_UNPAD_PKCS7_ERROR = "pad byte out of range"


def _sha1(*parts):
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def verify_event_signature(timestamp, nonce, encrypt_key, signature):
    """sha1(timestamp+nonce+encrypt_key)。"""
    if not signature:
        return False
    return _sha1(timestamp or "", nonce or "", encrypt_key or "") == signature


def verify_card_signature(timestamp, nonce, verification_token, signature):
    """sha1(timestamp+nonce+verification_token)。"""
    if not signature:
        return False
    return _sha1(timestamp or "", nonce or "", verification_token or "") == signature


def verify_token(body, verification_token):
    """verification_token 兜底(事件订阅 body 里带 token 时)。"""
    return bool(verification_token) and body.get("token") == verification_token


def decrypt(encrypt_key, payload_b64):
    """AES-256-CBC 解密飞书加密载荷。

    key = sha256(encrypt_key); 密文 = base64(iv[16] + AES 密文); PKCS7 去填充。
    """
    from Crypto.Cipher import AES
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    raw = base64.b64decode(payload_b64)
    iv, ct = raw[:16], raw[16:]
    pt = AES.new(key, AES.MODE_CBC, iv).decrypt(ct)
    pad = pt[-1]
    if 0 < pad <= 16:
        return pt[:-pad].decode("utf-8")
    return pt.decode("utf-8", "ignore")


def encrypt_for_test(encrypt_key, plaintext):
    """仅测试用:按飞书格式加密(AES-256-CBC, iv 前置)。"""
    import os
    from Crypto.Cipher import AES
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv = os.urandom(16)
    pad = 16 - len(plaintext) % 16
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(plaintext.encode("utf-8") + bytes([pad]) * pad)
    return base64.b64encode(iv + ct).decode("ascii")
