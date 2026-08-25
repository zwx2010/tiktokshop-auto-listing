"""Bridge —— 把任务翻译成 `claude -p` 无头调用,解析封套 JSON 结果。

为什么用 subprocess 调 claude CLI 而不是 SDK:
  claude 即在 PATH(当前环境实测 2.1.226),无额外依赖;
  skills 是全局的,claude -p 直接能加载;编排层只负责"翻译任务 + 收结果"。

实测 `--output-format json` 返回单个封套对象:
  {"result": "<助手最终文本>", "is_error": false, "session_id": "...",
   "total_cost_usd": .., "usage": {...}, "permission_denials": [], ...}
所以 stage_result = 对 envelope["result"] 做宽松 JSON 解析(纯文本则原样返回)。
"""
import json
import os
import re
import shutil
import subprocess

from ..config import BASE_DIR

DEFAULT_TIMEOUT_S = int(os.environ.get("AGENT_TIMEOUT_S", "300"))


def claude_bin():
    env = os.environ.get("CLAUDE_BIN")
    if env:
        return env
    return shutil.which("claude")


def _find_json_object(text):
    """按括号配平扫描,找第一个能解析的顶层 JSON 对象。

    比 `{.*}` 贪婪正则稳:能容忍 JSON 前后/中间夹着说明文字、多个 JSON 块、
    summary 里带花括号等情况。找不到返回 None。
    """
    stack, start = [], -1
    for i, ch in enumerate(text):
        if ch == "{":
            if start < 0:
                start = i
            stack.append(i)
        elif ch == "}":
            if stack:
                stack.pop()
                if not stack and start >= 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        start = -1  # 这块不是合法 JSON,继续找下一块
    return None


def _parse_json_lenient(text):
    """整体 parse → 失败剥 markdown 围栏 → 再失败括号配平找对象 → 兜底原文。"""
    t = (text or "").strip()
    if not t:
        return None
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    obj = _find_json_object(t)
    if obj is not None:
        return obj
    return t


def run_claude(prompt, *, timeout_s=DEFAULT_TIMEOUT_S, cwd=None, extra_args=None,
               env_extra=None):
    """调用 claude -p 无头跑一个任务。

    返回:
      {ok, stage_result, session_id, cost_usd, usage, error, exit_code}
    stage_result:尽量解析成对象/文本;ok = 进程 0 且封套 is_error 为假。
    """
    bin = claude_bin()
    if not bin:
        return {"ok": False, "stage_result": None, "error": "claude CLI not found (设 CLAUDE_BIN 或加入 PATH)",
                "session_id": None, "cost_usd": None, "usage": None,
                "exit_code": None}
    # 注意: Windows 上 claude_bin 常是 .CMD 垫片,经 cmd.exe 解析会把 `-p` 参数里
    # 的多行内容截断到第一行(实测)。因此 prompt 一律走 stdin: claude -p 无参数时读 stdin。
    args = [bin, "-p", "--output-format", "json",
            "--permission-mode", "bypassPermissions",
            "--allow-dangerously-skip-permissions",
            "--dangerously-skip-permissions"]
    if extra_args:
        args += extra_args
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(args, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout_s, cwd=cwd or str(BASE_DIR), env=env)
    except FileNotFoundError:
        return {"ok": False, "stage_result": None, "error": "claude CLI not found",
                "session_id": None, "cost_usd": None, "usage": None, "exit_code": None}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage_result": None, "error": f"超时 {timeout_s}s",
                "session_id": None, "cost_usd": None, "usage": None, "exit_code": None}

    err = (proc.stderr or "").strip()[:800]
    envelope = None
    try:
        envelope = json.loads((proc.stdout or "").strip())
    except Exception:
        pass

    if isinstance(envelope, dict) and "result" in envelope:
        is_err = proc.returncode != 0 or bool(envelope.get("is_error"))
        return {
            "ok": not is_err,
            "stage_result": _parse_json_lenient(envelope.get("result")),
            "session_id": envelope.get("session_id"),
            "cost_usd": envelope.get("total_cost_usd"),
            "usage": envelope.get("usage"),
            # 只有真失败才填 error(进程非0/封套 is_error);成功时 result 是正常产物,
            # 塞进 error 会被上游 _stage_err 误判成"环节失败",卡上乱标 ⚠️。
            "error": (err or str(envelope.get("result"))[:300]) if is_err else "",
            "exit_code": proc.returncode,
        }

    # 非封套输出(异常/旧版),回退整段 stdout
    text = (proc.stdout or "").strip()
    is_err = proc.returncode != 0
    return {"ok": proc.returncode == 0,
            "stage_result": _parse_json_lenient(text),
            "session_id": None, "cost_usd": None, "usage": None,
            "error": err or (text[:300] if is_err else ""),
            "exit_code": proc.returncode}
