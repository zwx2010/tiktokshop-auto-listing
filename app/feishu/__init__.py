"""飞书接入层。

- client.py: 发文本/交互卡片到群(自定义机器人 webhook,零管理员即可用)
- signature.py: 回调签名校验 + 加密载荷 AES 解密
- handlers.py: 审批/消息 handler 注册槽位(M3 编排层接线)
"""
from . import client, handlers, signature  # noqa

__all__ = ["client", "signature", "handlers"]
