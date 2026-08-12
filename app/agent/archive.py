"""采集商品删除服务 —— 飞书选品表「删除商品」按钮背后的平台侧动作。

软删 + 连带处理:
  1. 平台库 Product.active = False(数据保留可回溯,不再出表/上架)
  2. 删掉选品表(表A)对应行
  3. 上架表(表B)同 SPU 的「待上架」任务标「上架失败」
     (在跑的中间态行由流水线 active 过滤在处理时如实标失败)
成功时发一张结果卡到审批群留痕。
"""
from ..database import SessionLocal
from ..feishu import bitable
from ..feishu import client as fc
from ..models import Product


def delete_product(spu=None, goods_id=None, notify=True):
    """按 SPU(优先)或 商品ID 软删一个采集商品。返回 {"ok": bool, ...}。"""
    spu = str(spu or "").strip()
    gid = str(goods_id or "").strip()
    if not spu and not gid:
        return {"ok": False, "msg": "未提供 SPU/商品ID,无法定位商品"}

    db = SessionLocal()
    try:
        p = None
        if spu:
            p = db.query(Product).filter(Product.spu == spu).first()
        if p is None and gid:
            p = db.query(Product).filter(Product.source_goods_id == gid).first()
        if p is None:
            return {"ok": False,
                    "msg": f"SPU/商品ID 不在采集库(spu={spu or '-'} gid={gid or '-'})"}
        if not p.active:
            return {"ok": False, "msg": f"{p.spu} 已在删除状态,无需重复操作"}
        gid = gid or str(p.source_goods_id or "")
        p.active = False
        db.commit()
    finally:
        db.close()

    reason = f"商品 {p.spu} 已在采集表删除"
    try:
        pick_deleted = bitable.delete_pick_by_goods_id(gid)
    except Exception as exc:
        pick_deleted = -1
        reason += f";选品表行删除失败({exc})"
    try:
        tasks_failed = bitable.mark_listing_pending_failed(spu, reason)
    except Exception as exc:
        tasks_failed = -1
        reason += f";上架表待上架任务标记失败({exc})"

    result = {
        "ok": True,
        "spu": p.spu,
        "gid": gid,
        "title": p.title_cn or "",
        "pick_rows_deleted": pick_deleted,
        "tasks_failed": tasks_failed,
    }
    if notify:
        _notify(result)
    return result


def _notify(result) -> None:
    """发一张结果卡到审批群留痕(失败也如实反映)。"""
    try:
        fields = [
            ("结果", "已删除(软删,可回溯)"),
            ("SPU", result.get("spu") or "-"),
            ("商品ID", result.get("gid") or "-"),
            ("标题", str(result.get("title") or "")[:60] or "-"),
            ("选品表行", f"移除 {result.get('pick_rows_deleted', 0)} 行"),
            ("上架任务", f"标记失败 {result.get('tasks_failed', 0)} 条"),
        ]
        fc.deliver_card(fc.approval_card(
            title="商品已删除", color="red", fields=fields, buttons=[]))
    except Exception as exc:
        print(f"[archive] 发结果卡失败: {exc}", flush=True)
