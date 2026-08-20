from __future__ import annotations

from dataclasses import dataclass

from ..domain.entities import ListingAggregate, TaskAggregate
from ..domain.errors import InvalidStateTransition, TaskAlreadyClaimed


@dataclass
class ListingService:
    """Listing 状态和幂等规则；后续由 Repository 替换内存存储。"""

    listings: dict[tuple[int, int, str], ListingAggregate] | None = None

    def __post_init__(self) -> None:
        if self.listings is None:
            self.listings = {}

    def create(self, *, product_id: int, account_id: int, market_code: str,
               seller_sku: str) -> ListingAggregate:
        key = (product_id, account_id, market_code.upper())
        if key not in self.listings:
            self.listings[key] = ListingAggregate(
                product_id=product_id,
                account_id=account_id,
                market_code=market_code.upper(),
                seller_sku=seller_sku,
            )
        return self.listings[key]

    def mark_ready(self, listing: ListingAggregate) -> ListingAggregate:
        if listing.status != "draft":
            raise InvalidStateTransition(f"listing {listing.status} cannot become ready")
        if listing.warning_count:
            raise InvalidStateTransition("listing with quality warnings cannot become ready")
        listing.status = "ready"
        return listing


@dataclass
class TaskService:
    tasks: list[TaskAggregate] | None = None

    def __post_init__(self) -> None:
        if self.tasks is None:
            self.tasks = []

    def create(self, *, task_type: str, max_attempts: int = 3) -> TaskAggregate:
        task = TaskAggregate(task_type=task_type, max_attempts=max_attempts, id=len(self.tasks) + 1)
        self.tasks.append(task)
        return task

    def claim(self, task: TaskAggregate, *, owner: str) -> TaskAggregate:
        if task.status != "pending" or task.owner is not None:
            raise TaskAlreadyClaimed(f"task {task.id} is already claimed")
        task.status = "running"
        task.owner = owner
        task.attempts += 1
        task.logs.append(f"claimed:{owner}")
        return task

    def finish(self, task: TaskAggregate, *, owner: str) -> TaskAggregate:
        if task.status != "running" or task.owner != owner:
            raise InvalidStateTransition("only the active owner can finish a running task")
        task.status = "success"
        task.logs.append("finished:success")
        return task
