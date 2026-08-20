"""Truthful outcomes shared by external integration adapters."""

from dataclasses import dataclass, field
from typing import Any


INTEGRATION_STATUSES = frozenset({
    "not_configured",
    "configured",
    "unavailable",
    "failed",
    "confirmed",
})


@dataclass(frozen=True)
class IntegrationOutcome:
    provider: str
    status: str
    operation: str
    message: str = ""
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in INTEGRATION_STATUSES:
            raise ValueError(f"unsupported integration status: {self.status}")

    @property
    def is_success(self) -> bool:
        return self.status == "confirmed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "operation": self.operation,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


def credential_status(provider: str, operation: str, required: dict[str, str]) -> IntegrationOutcome:
    """Report configuration without exposing credential values or claiming connectivity."""
    missing = sorted(name for name, value in required.items() if not str(value or "").strip())
    if missing:
        return IntegrationOutcome(
            provider=provider,
            status="not_configured",
            operation=operation,
            message="required credentials or runtime settings are missing",
            details={"missing": missing},
        )
    return IntegrationOutcome(
        provider=provider,
        status="configured",
        operation=operation,
        message="configuration is present; provider connectivity has not been verified",
    )
