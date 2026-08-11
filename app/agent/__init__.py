"""Agent 编排层:把任务翻译成 `claude -p` 无头调用。

- bridge.py:claude CLI 调用 + 封套 JSON 解析(失败隔离/超时捕获)
- tasks.py:分阶段任务定义(采集→制表→审图→审批→上架),每个 stage 一个调用
"""
from . import bridge, tasks  # noqa

__all__ = ["bridge", "tasks"]
