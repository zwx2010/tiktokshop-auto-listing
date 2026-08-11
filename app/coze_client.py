"""AI 文案客户端 —— 只走真实文案源，禁止 Mock 造假文案。

两个真实渠道：
- DictCozeClient：字典直供，灌入已生成/人工/真实产出的文案（codex 表）；
- HttpCozeClient：调真实 Coze 机器人 HTTP API 生成。

get_coze_client() 工厂不再提供离线 Mock 兜底：
未配 codex 数据、也未设 COZE_API_TOKEN 时直接报错，绝不静默编造文案。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

import requests

DEFAULT_STORE = "RoseSeek"


@dataclass
class CopyResult:
    title: str = ""
    description: str = ""
    keywords: str = ""
    styles_en: list = field(default_factory=list)
    source: str = ""  # dict / coze；空表示未生成


class CozeClient(Protocol):
    def generate_copy(self, ctx: dict, market: str) -> CopyResult: ...


class DictCozeClient:
    """字典直供：把已生成的（或人工/真实 Coze 产出的）文案灌进来。

    data 形如 { "<MARKET>|<goods_id>": {"title": ..., "description": ..., "styles_en": [...]} }
    key 未命中 → 报错（不许静默编造文案）。
    """

    def __init__(self, data: dict):
        self._data = data

    def generate_copy(self, ctx: dict, market: str) -> CopyResult:
        key = f"{market.upper()}|{ctx.get('goods_id', '')}"
        row = self._data.get(key) or self._data.get(ctx.get("goods_id", ""))
        if not row:
            raise RuntimeError(
                f"DictCozeClient 未命中真实文案: market={market} goods_id={ctx.get('goods_id')}"
                f" —— 无 codex 文案，拒绝编造"
            )
        return CopyResult(
            title=row.get("title", ""),
            description=row.get("description", ""),
            keywords=row.get("keywords", ""),
            styles_en=row.get("styles_en", []),
            source="dict",
        )


class HttpCozeClient:
    """Coze 机器人 HTTP 接入（真实场景）。

    用法：
        set COZE_API_TOKEN=pat_xxx
        set COZE_BOT_ID=73xxxxxx
    请求体/响应体按你的 Coze 工作流调整即可。
    """

    def __init__(self, token: str = "", bot_id: str = "", endpoint: str = ""):
        self.token = token or os.environ.get("COZE_API_TOKEN", "")
        self.bot_id = bot_id or os.environ.get("COZE_BOT_ID", "")
        self.endpoint = endpoint or "https://api.coze.cn/open_api/v2/chat"

    def generate_copy(self, ctx: dict, market: str) -> CopyResult:
        if not self.token:
            raise RuntimeError("COZE_API_TOKEN not set")
        payload = {
            "bot_id": self.bot_id,
            "user": "tiktok-platform",
            "query": self._build_prompt(ctx, market),
        }
        resp = requests.post(
            self.endpoint,
            headers={"Authorization": f"Bearer {self.token}"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["data"]["content"]  # 依实际响应结构调整
        return CopyResult(title="", description=text, source="coze")

    def _build_prompt(self, ctx: dict, market: str) -> str:
        return (
            f"为 {market} 站点生成 TikTok 商品标题与描述。中文源标题：{ctx.get('source_title_cn')}。"
            f"类目：{ctx.get('category')}。颜色：{ctx.get('colors')}。款式：{ctx.get('styles')}。"
            f"店铺名 {ctx.get('store_name')}，标题需以 {ctx.get('store_name')} 开头、以 Style {ctx.get('style_code')} 结尾。"
        )


def get_coze_client(data: dict | None = None) -> CozeClient:
    """工厂：只返回真实文案源。

    - 传 data（codex 文案 dict）→ DictCozeClient；
    - 设了 COZE_API_TOKEN → HttpCozeClient；
    - 都没有 → 抛错，禁止用 Mock 造假的兜底文案。
    """
    if data is not None:
        return DictCozeClient(data)
    if os.environ.get("COZE_API_TOKEN"):
        return HttpCozeClient()
    raise RuntimeError(
        "未配置真实文案源：需传入 codex 文案数据 或 设置 COZE_API_TOKEN/COZE_BOT_ID，"
        "拒绝生成 Mock 假文案"
    )
