"""时间工具 —— 把数据库里的 UTC 时间显示成北京时间（东八区）。

背景：SQLite 的 func.now() 存的是 UTC（比北京慢 8 小时），直接展示会让
      采集时间看起来"早了 8 小时"。统一在展示层转北京时间。
"""
from datetime import datetime, timedelta, timezone

# 北京时间 = UTC+8
_BEIJING = timezone(timedelta(hours=8))


def utc_to_local(dt: datetime | None) -> datetime | None:
    """naive UTC datetime -> 北京时间（带 +8 时区）。"""
    if not dt:
        return None
    if dt.tzinfo is None:
        # SQLite 读出的是 naive，按 UTC 处理
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BEIJING)


def fmt_utc(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """直接输出北京时间字符串（展示用）。"""
    local = utc_to_local(dt)
    return local.strftime(fmt) if local else ""
