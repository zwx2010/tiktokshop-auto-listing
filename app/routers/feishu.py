"""飞书接入路由。

- POST /api/feishu/webhook      事件订阅(消息/审批/URL 验证握手)
- POST /api/feishu/card         交互卡片按钮回调(通过/驳回 → handlers.on_card_action)
- POST|GET /api/feishu/table-delete  选品表「删除商品」按钮 → 自动化流程 HTTP 请求

配置: config/feishu.local.json
  {app_id, app_secret, encrypt_key, verification_token, group_bot_webhook}
签名: sha1(timestamp+nonce+encrypt_key),verification_token 兜底。
本地调试可用 env ALLOW_UNVERIFIED=1 跳过校验(默认关闭;生产必须校验)。
"""
import json
import os
import threading

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from ..config import CONFIG_DIR
from ..feishu import handlers, signature as sig
from ..rag.io import read_json

router = APIRouter()

# 已处理过的消息 event_id,防飞书重试造成重复 run(同一事件只跑一次流水线)
_MSG_DEDUP = set()
_DEDUP_LOCK = threading.Lock()


def _dispatch_message(body):
    """后台线程跑消息处理(流水线耗时几分钟),失败不影响回调响应。"""
    try:
        handlers.handle_message(body)
    except Exception as exc:
        print(f"[webhook] dispatch error: {exc}", flush=True)


def _cfg():
    p = CONFIG_DIR / "feishu.local.json"
    return read_json(p) if p.is_file() else {}


def _verified(timestamp, nonce, signature, body=None):
    """通过则返回 True;否则 False(调用方回 401)。
    卡片回调 schema 2.0 把验证令牌放 body(header.token/token),头签名缺失时兜底比对。"""
    if os.environ.get("ALLOW_UNVERIFIED") == "1":
        return True
    cfg = _cfg()
    enc = cfg.get("encrypt_key", "")
    tok = cfg.get("verification_token", "")
    if sig.verify_event_signature(timestamp, nonce, enc, signature):
        return True
    if tok and sig.verify_card_signature(timestamp, nonce, tok, signature):
        return True
    if body is not None and tok:
        body_token = (body.get("header") or {}).get("token") or body.get("token")
        if body_token and body_token == tok:
            return True
    return False


def _body_bytes(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def _decision_toast(result):
    """把状态机结果映射成点击后的飞书 toast 文案;无明确结果返回 None(不弹)。

    上架是后台异步的(可能耗时数分钟,真实 CDP 上传),回调立即回「进行中」;
    真实 ok/fail 由后台上架完成后的「上架结果卡」回推,这里不谎报"完成"。"""
    if not result or not result.get("ok"):
        return None
    status = result.get("status", "")
    if status == "approved":
        if result.get("upload_status") == "running":
            return "已通过,上架进行中(完成推结果卡)"
        return "已通过,上架已完成"
    if status == "rejected":
        return "已驳回,流程中止"
    return None


def _unwrap_encrypted(body, encrypt_key):
    """Encrypt Key 启用时解开 body.encrypt;返回业务 JSON。"""
    if "encrypt" not in body:
        return body
    try:
        return json.loads(sig.decrypt(encrypt_key, body["encrypt"]))
    except Exception as exc:
        raise ValueError(f"decrypt fail: {exc}") from exc


async def _read_and_verify(request):
    raw = await request.body()
    ts = request.headers.get("X-Lark-Request-Timestamp", "")
    nonce = request.headers.get("X-Lark-Request-Nonce", "")
    sign = request.headers.get("X-Lark-Signature", "")
    body = _body_bytes(raw)
    # 飞书事件订阅校验请求只携带 challenge/token，通常没有事件签名。
    # 先回显 challenge，避免后台把 401/隧道错误页判为“非法 JSON”。
    # 正常业务事件仍必须经过下方签名/令牌校验。
    if body.get("type") == "url_verification" and body.get("challenge"):
        return raw, None
    if not _verified(ts, nonce, sign, body):
        return None, {"ok": False, "detail": "signature mismatch"}
    return raw, None


@router.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    raw, err = await _read_and_verify(request)
    if err:
        return JSONResponse(status_code=401, content=err)
    body = _body_bytes(raw)
    try:
        body = _unwrap_encrypted(body, _cfg().get("encrypt_key", ""))
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "detail": str(exc)})

    # URL 验证握手:飞书订阅回调地址时,回显 challenge
    if body.get("type") == "url_verification":
        print("[webhook] url_verification 握手", flush=True)
        return {"challenge": body.get("challenge")}

    event = body.get("event") or {}
    header = body.get("header") or {}
    etype = event.get("type") or header.get("event_type") or body.get("type")
    print(f"[webhook] type={body.get('type')} event.type={etype} "
          f"body={json.dumps(body, ensure_ascii=False)[:200]}", flush=True)
    # 群消息:流水线耗时几分钟,丢后台线程跑并立即回 200,避免阻塞事件循环
    # 触发飞书重试(同一事件重复 run)。用 header.event_id 去重。
    if etype == "im.message.receive_v1":
        eid = header.get("event_id") or event.get("event_id") or ""
        if eid:
            with _DEDUP_LOCK:
                if eid in _MSG_DEDUP:
                    print(f"[webhook] dup event {eid}, skip", flush=True)
                    return {"code": 0}
                _MSG_DEDUP.add(eid)
        threading.Thread(target=_dispatch_message, args=(body,), daemon=True).start()
        return {"code": 0}
    return handlers.handle_message(body)


@router.post("/feishu/card")
async def feishu_card(request: Request):
    raw, err = await _read_and_verify(request)
    if err:
        return JSONResponse(status_code=401, content=err)
    body = _body_bytes(raw)
    try:
        body = _unwrap_encrypted(body, _cfg().get("encrypt_key", ""))
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"ok": False, "detail": str(exc)})

    # 保存卡片回调地址时,飞书发 url_verification 握手 → 回显 challenge
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge")}

    # 正常按钮回调:推进状态机(结果在内部记录/可查);按飞书契约回 code=0
    try:
        result = handlers.handle_card_action(body)
    except Exception as exc:  # 回调解析异常不崩端点,记录并回 code=0
        result = {"ok": False, "error": str(exc)}
    print(f"[card-callback] body={json.dumps(body, ensure_ascii=False)[:400]} "
          f"-> {json.dumps(result, ensure_ascii=False)[:200]}", flush=True)
    # 点击后给飞书 toast 反馈(否则"点了没反应");幂等/未知 run 不弹
    toast = _decision_toast(result)
    resp = {"code": 0, "msg": "success"}
    if toast:
        resp["toast"] = {"type": "success", "content": toast}
    return resp


@router.post("/feishu/table-delete")
@router.get("/feishu/table-delete")
async def feishu_table_delete(request: Request):
    """选品表「删除商品」按钮的落点 —— 多维表格自动化流程发 HTTP 请求到这里。

    自动化流程的请求没有飞书事件签名头,改为校验 body/query 里的
    token == config 的 verification_token(本地调试 ALLOW_UNVERIFIED=1 放行)。
    动作 = 软删商品 + 删选品表行 + 上架表待上架任务标失败,见 app/agent/archive.py。
    """
    raw = await request.body()
    body = _body_bytes(raw)
    query = request.query_params
    token = body.get("token") or query.get("token")
    spu = str(body.get("spu") or query.get("spu") or "").strip()
    gid = str(body.get("goods_id") or query.get("goods_id") or "").strip()
    if os.environ.get("ALLOW_UNVERIFIED") != "1":
        cfg_tok = _cfg().get("verification_token", "")
        if not cfg_tok or token != cfg_tok:
            return {"code": 1, "msg": "token 校验失败"}
    from ..agent import archive
    result = archive.delete_product(spu=spu, goods_id=gid)
    if result.get("ok"):
        return {"code": 0,
                "msg": f"已删除 {result.get('spu')}:选品表移除 "
                       f"{result.get('pick_rows_deleted')} 行,上架表标记失败 "
                       f"{result.get('tasks_failed')} 条"}
    return {"code": 1, "msg": result.get("msg") or "删除失败"}
