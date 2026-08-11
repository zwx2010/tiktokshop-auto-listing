"""审批/消息 handler 注册槽位 —— 由 M3 编排层接线,这里只留注册位。

默认 no-op 保证 M2 路由可独立测试;M3 用 set_handlers 注入真实审批状态机:
  on_message(body)      → 收到群消息/事件(关键词"通过/驳回"降级路径)
  on_card_action(body)  → 收到审批卡按钮回调(通过/驳回 → 推进状态机)
"""
_handlers = {"on_message": None, "on_card_action": None}


def set_handlers(on_message=None, on_card_action=None):
    if on_message is not None:
        _handlers["on_message"] = on_message
    if on_card_action is not None:
        _handlers["on_card_action"] = on_card_action
    return _handlers


def get_handlers():
    return dict(_handlers)


def handle_message(body):
    h = _handlers["on_message"]
    if h:
        return h(body)
    return {"ok": False, "detail": "on_message handler not wired (M3)"}


def handle_card_action(body):
    h = _handlers["on_card_action"]
    if h:
        return h(body)
    return {"ok": False, "detail": "on_card_action handler not wired (M3)"}
