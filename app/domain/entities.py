from dataclasses import dataclass, field


@dataclass
class ListingAggregate:
    product_id: int
    account_id: int
    market_code: str
    seller_sku: str
    status: str = "draft"
    warning_count: int = 0
    id: int | None = None


@dataclass
class TaskAggregate:
    task_type: str
    status: str = "pending"
    attempts: int = 0
    max_attempts: int = 3
    owner: str | None = None
    logs: list[str] = field(default_factory=list)
    id: int | None = None
