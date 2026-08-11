"""共享 IO:带 BOM 容错的 JSON 读取 + 工作流目录解析。

工作流包目录解析复用平台既有规则:
`TIKTOK_WORKFLOW_DIR` 环境变量优先,否则取 BASE_DIR 同级 `tk自动化工作流`。
"""
import json
import os
from pathlib import Path

from ..config import BASE_DIR


def read_json(path):
    """读 JSON,容忍 UTF-8 BOM(PowerShell 写的配置常带 BOM)。"""
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig") if raw.startswith(b"\xef\xbb\xbf") else raw.decode("utf-8")
    return json.loads(text)


def workflow_dir():
    """返回含 config/ 的工作流包目录(含规则配置)。"""
    env = os.environ.get("TIKTOK_WORKFLOW_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    candidates = [
        BASE_DIR.parent / "tk自动化工作流",
        Path(r"D:\ccproject\codeproject\RoseSeek_TikTokShop_AI_Localized_20260809"),
    ]
    for c in candidates:
        if (c / "config").is_dir():
            return c
    return BASE_DIR.parent / "tk自动化工作流"
