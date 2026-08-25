from __future__ import annotations

import re

_SENSITIVE = re.compile(r"(?i)(authorization|access[_-]?token|refresh[_-]?token|app[_-]?secret|password|cookie)\s*[:=]\s*(?:bearer\s+)?([^,;\s}]+)")


def redact(value, *, replacement="[REDACTED]"):
    """Return a log-safe representation without credentials or session cookies."""
    text = str(value)
    return _SENSITIVE.sub(lambda m: f"{m.group(1)}={replacement}", text)
