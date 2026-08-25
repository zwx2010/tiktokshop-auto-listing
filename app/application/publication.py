"""Explicit human approval gate for bounded test publication."""
from __future__ import annotations

MAX_TEST_PRODUCTS = 50
REQUIRED_PROBES = ("feishu", "ai", "cdp", "tiktok")


def validate_test_release(*, candidate_count: int, account: str, market: str,
                          probes: dict[str, bool], approved: bool,
                          rollback_plan: str) -> dict:
    failed = []
    if candidate_count < 1 or candidate_count > MAX_TEST_PRODUCTS:
        failed.append("candidate_count")
    if not account:
        failed.append("account")
    if market not in {"ph", "th", "vn"}:
        failed.append("market")
    failed.extend(name for name in REQUIRED_PROBES if not probes.get(name))
    if not approved:
        failed.append("human_approval")
    if not rollback_plan.strip():
        failed.append("rollback_plan")
    return {"eligible": not failed, "failed_gates": failed,
            "max_test_products": MAX_TEST_PRODUCTS}
