class DomainError(Exception):
    """可安全转换为业务错误响应的领域异常。"""


class InvalidStateTransition(DomainError):
    pass


class TaskAlreadyClaimed(DomainError):
    pass
