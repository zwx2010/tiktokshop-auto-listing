"""Persist copy-provider results without allowing blank text to become ready."""

from datetime import datetime, timezone


def record_copy_result(
    listing,
    *,
    title: str,
    description: str,
    source: str,
    valid: bool,
    reason: str = "",
):
    """Apply one auditable copy result to a Listing-like object."""
    listing.copy_checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    listing.copy_source = (source or "").strip()
    if valid and (title or "").strip() and (description or "").strip():
        listing.title = title.strip()
        listing.description = description.strip()
        listing.listing_status = "ready"
        listing.copy_reason = ""
    else:
        listing.title = ""
        listing.description = ""
        listing.listing_status = "copy_missing"
        listing.copy_reason = (reason or "copy provider returned no valid copy").strip()
    return listing
