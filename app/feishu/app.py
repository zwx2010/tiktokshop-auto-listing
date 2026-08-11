"""飞书自建应用客户端 —— 用 app_id/app_secret 走开放平台 API。

为什么要有这个:群机器人 webhook 只能发不能收。审批卡按钮要真回调,
卡必须由「自建应用」通过 API 发(应用发的交互卡,按钮才带回调地址),
并在应用后台配卡片回调 URL → /api/feishu/card。

权限需求(应用后台开通):
  im:message   以应用身份发消息/卡片
  im:chat      获取群列表(按群名找 chat_id)
"""
import json
import threading
import time

import requests

from ..config import CONFIG_DIR
from ..rag.io import read_json

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
CHAT_LIST_URL = "https://open.feishu.cn/open-apis/im/v1/chats"
MSG_SEND_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

_lock = threading.Lock()
_token_cache = {}  # app_id -> {token, expire_at}


def app_config():
    p = CONFIG_DIR / "feishu.local.json"
    return read_json(p) if p.is_file() else {}


def get_tenant_access_token(app_id=None, app_secret=None):
    """获取 tenant_access_token(缓存到过期前 60s)。返回 (token, err)。"""
    cfg = app_config()
    app_id = app_id or cfg.get("app_id")
    app_secret = app_secret or cfg.get("app_secret")
    if not app_id or not app_secret:
        return None, "app_id/app_secret 未配置(先建自建应用并填 feishu.local.json)"
    with _lock:
        c = _token_cache.get(app_id)
        if c and c["expire_at"] > time.time() + 60:
            return c["token"], ""
    try:
        r = requests.post(TOKEN_URL,
                          json={"app_id": app_id, "app_secret": app_secret},
                          timeout=10)
        d = r.json()
    except Exception as exc:
        return None, f"token 请求异常: {exc}"
    if d.get("code") != 0:
        return None, f"token 失败: {d.get('code')} {d.get('msg')}"
    tok = d["tenant_access_token"]
    with _lock:
        _token_cache[app_id] = {"token": tok,
                                "expire_at": time.time() + int(d.get("expire", 7200)) - 60}
    return tok, ""


def list_chats(token, page_size=100):
    """获取群列表。返回 (items, err)。"""
    try:
        r = requests.get(CHAT_LIST_URL, params={"page_size": page_size},
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
        d = r.json()
    except Exception as exc:
        return [], str(exc)
    if d.get("code") != 0:
        return [], f"{d.get('code')} {d.get('msg')}"
    return d.get("data", {}).get("items", []), ""


def find_chat_id_by_name(token, name):
    """按群名精确匹配 chat_id;找不到返回 None。"""
    if not name:
        return None
    items, err = list_chats(token)
    if err:
        return None
    for it in items:
        if it.get("name") == name:
            return it.get("chat_id")
    return None


def send_card_app(token, chat_id, card):
    """用应用身份发交互卡到群(按钮带回调)。card 是 dict。返回 (ok, resp)。"""
    try:
        r = requests.post(
            MSG_SEND_URL, params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={"receive_id": chat_id, "msg_type": "interactive",
                  "content": json.dumps(card, ensure_ascii=False)},
            timeout=10)
        d = r.json()
    except Exception as exc:
        return False, {"error": str(exc)}
    return d.get("code") == 0, d


def resolve_chat_id(token, group_name=None):
    """解析发卡目标群 chat_id:配置显式 chat_id > 按 approval_group 群名查。"""
    cfg = app_config()
    cid = cfg.get("chat_id") or ""
    if cid:
        return cid, ""
    name = group_name or cfg.get("approval_group") or ""
    cid = find_chat_id_by_name(token, name)
    if cid:
        return cid, ""
    return None, f"找不到群「{name}」(确认机器人已在群里,且授予了 im:chat 权限)"
