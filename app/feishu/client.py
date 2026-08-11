"""飞书客户端 —— 发文本/交互卡片到群(自定义机器人 webhook)。

用 webhook 就能"发"(建群机器人即可,不需要企业管理员);
"收"(审批按钮回调)需要自建应用,见 signature/handlers + M3 编排。

配置: config/feishu.local.json {group_bot_webhook: "https://open.feishu.cn/open-apis/bot/v2/hook/..."}
"""
import requests

from ..config import CONFIG_DIR
from ..rag.io import read_json

DEFAULT_WEBHOOK_BASE = "https://open.feishu.cn/open-apis/bot/v2/hook/"


def feishu_config():
    p = CONFIG_DIR / "feishu.local.json"
    return read_json(p) if p.is_file() else {}


def send_payload(webhook, payload, timeout=10):
    """POST 到机器人 webhook,返回 {status, body}。webhook 为空则不下发。"""
    if not webhook:
        return {"status": 0, "body": {"error": "group_bot_webhook 未配置"}}
    try:
        r = requests.post(webhook, json=payload, timeout=timeout)
        data = r.json() if r.content else {}
        return {"status": r.status_code, "body": data}
    except Exception as exc:  # 网络不稳,不外抛
        return {"status": 0, "body": {"error": str(exc)}}


def send_text(webhook, text):
    return send_payload(webhook, {"msg_type": "text", "content": {"text": text}})


def send_card(webhook, card):
    return send_payload(webhook, {"msg_type": "interactive", "card": card})


def deliver_card(card, group_name=None):
    """智能路由发审批卡:配了 app_id/secret → 用应用 API 发(按钮带回调);
    没配 → 退回群机器人 webhook(只能发,按钮回调无效)。"""
    from . import app as app_client
    cfg = feishu_config()
    if cfg.get("app_id") and cfg.get("app_secret"):
        token, err = app_client.get_tenant_access_token()
        if err:
            return {"status": 0, "body": {"error": err}, "via": "app"}
        chat_id, cerr = app_client.resolve_chat_id(token, group_name)
        if not chat_id:
            return {"status": 0, "body": {"error": cerr}, "via": "app"}
        ok, resp = app_client.send_card_app(token, chat_id, card)
        return {"status": 200 if ok else 0, "body": resp, "via": "app"}
    webhook = cfg.get("group_bot_webhook")
    if webhook:
        return {**send_card(webhook, card), "via": "webhook"}
    return {"status": 0, "body": {"error": "既无 app_id 也无 group_bot_webhook,未发卡"}, "via": "none"}


def approval_card(title, fields, buttons, values=None, color="blue", **extra):
    """构造选品审批卡 JSON。

    fields: [(label, value), ...] → 两列展示(候选数/成本区间/审图FAIL/违禁命中/风格)
    buttons: ["通过全部", "仅通过审图OK", "驳回"] —— 第一个高亮 primary
    values: {按钮文本: 回调原样返回的 value} —— 状态机据此路由(M3)
    **extra: 忽略多余键(如 build_card 的 recommend),边界容错。
    """
    actions = []
    for i, label in enumerate(buttons):
        v = (values or {}).get(label, {"action": label})
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": label},
            "type": "primary" if i == 0 else "default",
            "value": v,
        })
    elements = [
        {"tag": "div", "fields": [
            {"is_short": True,
             "text": {"tag": "lark_md", "content": f"**{k}**\n{v}"}}
            for k, v in fields
        ]},
    ]
    if actions:  # 结果卡等无按钮场景跳过 action 元素
        elements.append({"tag": "action", "actions": actions})
    return {
        "header": {"template": color,
                   "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }
