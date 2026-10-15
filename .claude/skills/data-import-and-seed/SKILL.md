---
name: data-import-and-seed
description: Use when writing or running management commands that import, seed or export data (storefront product import, demo data, cultural events).
---

# Imports and seeds

## Existing commands
```bash
python manage.py seed_demo_data        # tenants/: idempotent demo company + owner
python manage.py seed_cultural_events  # demand/: idempotent placeholder events
python manage.py import_storefront_products --help   # inventory/: m18foods catalogue -> a company
```

## Rules for a new command (follow `inventory/management/commands/import_storefront_products.py`)
- **Idempotent.** Match on a stable external key (`Product.external_id`), update names/descriptions/prices, never overwrite stock. A re-run must be safe.
- Target an explicit company; wrap writes in `transaction.atomic()`.
- Remote sources rate-limit and drop connections: page small (limit=100), pause between pages, retry with backoff, set a timeout and a User-Agent.
- Products with stock get one `OPENING` `StockBatch` so the till can sell them (selling needs batches, not `stock_quantity`). Receiving a batch does not raise `Product.stock_quantity`; only stock movements do.
- Names: append pack size only when the name has no digits, to avoid duplicates.
- Write the command to be testable by splitting fetching (network) from importing, and mock the fetch in tests (`inventory/test_import_storefront.py`).
- Run against a throwaway DB (`DB_NAME=/tmp/dev.sqlite3`), never the committed `db.sqlite3`.
- Never print secrets or credentials in command output.
