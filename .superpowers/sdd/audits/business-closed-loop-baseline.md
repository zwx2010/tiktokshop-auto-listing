# Business Closed Loop Baseline Audit

Date: 2026-08-21

## Observed Live Evidence

- `/api/v1/health/live` → 200.
- `/api/v1/health/ready` → 200 against MySQL.
- `/api/stats` → accounts 3, products 189, SKUs 426, listings 269, tasks 0.
- `/api/products` and the dashboard show 186 active products.
- `/api/listings?limit=1000` → 269 listings: 224 `ready`, 45 `copy_missing`.
- The 45 `copy_missing` records have blank titles and must not be treated as publishable.
- All three accounts currently report API status `none`.
- Dashboard subtitle still says `Python + SQLite` although runtime uses MySQL.
- `/api/products/export` returns an XLSX response with HTTP 200.

## Root-Cause Notes

- `app/routers/pages.py` filters `Product.active == True` for the dashboard count.
- `app/routers/api.py` counts all products in `/api/stats`.
- `app/templates/index.html` contains a stale SQLite runtime label.

## Batch-1 Acceptance Baseline

After Batch 1, dashboard and `/api/stats` must report the same active product count, and the page must identify MySQL. No listing or external provider data is changed by this batch.
