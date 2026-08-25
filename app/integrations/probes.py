"""Non-publishing connectivity probes with expiring confirmation receipts."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

TTL_SECONDS = 24 * 60 * 60
_RECEIPTS: dict[str, dict] = {}


@dataclass(frozen=True)
class ProbeResult:
    name: str
    status: str
    checked_at: float
    detail: str = ""
    fingerprint: str = ""

    def as_dict(self):
        return {"name": self.name, "status": self.status,
                "checked_at": self.checked_at, "detail": self.detail,
                "fingerprint": self.fingerprint}


def _fingerprint(config) -> str:
    safe = "|".join(f"{k}={v}" for k, v in sorted((config or {}).items()))
    return hashlib.sha256(safe.encode()).hexdigest()[:16]


def probe(name: str, check, *, config=None, now=None) -> ProbeResult:
    """Run a caller-supplied read-only check; never invokes publish/upload."""
    stamp = time.time() if now is None else now
    fp = _fingerprint(config)
    try:
        detail = str(check() or "ok")
        result = ProbeResult(name, "confirmed", stamp, detail, fp)
    except Exception as exc:
        result = ProbeResult(name, "failed", stamp, str(exc)[:300], fp)
    _RECEIPTS[name] = result.as_dict()
    return result


def confirmed(name: str, *, config=None, now=None) -> bool:
    row = _RECEIPTS.get(name)
    if not row:
        return False
    stamp = time.time() if now is None else now
    return (row["status"] == "confirmed" and stamp - row["checked_at"] <= TTL_SECONDS
            and row["fingerprint"] == _fingerprint(config))


def probe_all(checks: dict, *, configs=None, now=None) -> list[ProbeResult]:
    return [probe(name, fn, config=(configs or {}).get(name), now=now)
            for name, fn in checks.items()]
