"""飞书自建应用自检脚本 —— 一次跑通「token → 找群 → 发测试卡 → 回填 chat_id」。

用法(平台根目录下):
  python -m scripts.feishu_setup                 # 全流程自检,含发一张测试卡
  python -m scripts.feishu_setup --write-chat-id # 找到审批群后把 chat_id 写回 feishu.local.json

前置(open.feishu.cn 自建应用后台):
  app_id / app_secret 已填进 config/feishu.local.json
  应用已添加「机器人」能力,并被拉进审批群
  权限: im:message(发消息) im:chat(查群列表)
  若开了 IP 白名单,需把出口 IP(内网穿透/本机公网 IP)加进去,否则 token 请求 10300 报错
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os  # noqa: E402
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from app.feishu import app as app_client  # noqa: E402
from app.feishu import client as fc  # noqa: E402
from app.rag.io import read_json  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "feishu.local.json"


def write_chat_id(chat_id):
    cfg = read_json(CONFIG_PATH)
    cfg["chat_id"] = chat_id
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"  已写回 config/feishu.local.json  chat_id={chat_id}")


def main():
    ap = argparse.ArgumentParser(description="飞书自建应用自检")
    ap.add_argument("--write-chat-id", action="store_true",
                    help="找到审批群后把 chat_id 写回 feishu.local.json")
    args = ap.parse_args()

    cfg = fc.feishu_config()
    print("=" * 64)
    print("飞书自建应用自检")
    print("=" * 64)
    print(f"app_id:        {cfg.get('app_id') or '(空)'}")
    print(f"app_secret:    {'已填' if cfg.get('app_secret') else '(空)'}")
    print(f"approval_group:{cfg.get('approval_group') or '(空,可用 --group 群名 替代)'}")
    print(f"chat_id:       {cfg.get('chat_id') or '(空)'}")

    # 1) tenant_access_token
    print("\n[1] 取 tenant_access_token ...")
    token, err = app_client.get_tenant_access_token()
    if not token:
        print(f"  [FAIL] 失败: {err}")
        print("  排查: app_id/app_secret 是否正确 / IP 白名单是否含出口IP / 应用是否启用")
        return 1
    print(f"  [OK] token 获取成功(前8位 {token[:8]}...)")

    # 2) 查群列表,定位审批群 chat_id
    print("\n[2] 拉群列表,定位审批群 ...")
    items, err = app_client.list_chats(token)
    if err:
        print(f"  [FAIL] 失败: {err}")
        print("  排查: 是否授予 im:chat 权限 / 机器人是否已在群里")
        return 1
    print(f"  共 {len(items)} 个群:")
    name = cfg.get("approval_group") or ""
    target = None
    for it in items:
        mark = "  <-- 匹配审批群" if (name and it.get("name") == name) else ""
        print(f"    {it.get('name') or '(无群名)'}: {it.get('chat_id')}{mark}")
        if name and it.get("name") == name:
            target = it.get("chat_id")
    if name and not target:
        print(f"  [FAIL] 没找到群「{name}」")
        print("  排查: 群名是否精确一致(含空格)/ 机器人是否已拉进该群 / 群类型是否应用可发")
        return 1
    chat_id = cfg.get("chat_id") or target
    if not chat_id:
        print("  [FAIL] 既没有 config.chat_id 也没能按 approval_group 匹配到群,无法发卡")
        return 1

    # 3) 用应用 API 发一张测试审批卡(带按钮)
    print(f"\n[3] 发测试审批卡到 {chat_id} ...")
    card = fc.approval_card(
        title="飞书自建应用测试卡",
        fields=[("状态", "连接正常"), ("chat_id", chat_id)],
        buttons=["测试通过"],
        values={"测试通过": {"action": "test_ok", "run_id": "setup-test"}},
        color="green",
    )
    ok, resp = app_client.send_card_app(token, chat_id, card)
    if ok:
        print("  [OK] 测试卡已发到审批群,去飞书群里看一眼、点一下按钮")
        print(f"    返回: {json.dumps(resp, ensure_ascii=False)[:160]}")
    else:
        print(f"  [FAIL] 发卡失败: {json.dumps(resp, ensure_ascii=False)[:300]}")
        print("  排查: 应用是否 im:message 权限 / chat_id 是否有效 / 群是否允许应用发消息")
        return 1

    # 4) 回填 chat_id
    if args.write_chat_id:
        write_chat_id(chat_id)

    print("\n自检通过 [OK]")
    print("下一步: 在应用后台「事件与回调-事件配置」填卡片回调 URL = 内网穿透地址 + /api/feishu/card")
    print("        (自建应用发的交互卡,按钮点击会把 value 回调到该地址,状态机据此审批)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
