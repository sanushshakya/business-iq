---
name: pricing-and-forecasting
description: Use when working on landed cost, decay markdowns, price-change approval and Shopify sync, demand alerts/forecasting, cultural-calendar events, or freight alerts.
---

# Pricing, demand and freight logic

Read `config/CLAUDE.md` for the full rules; the essentials:

## Pricing (`common/services/`, `pricing/`)
- `CostCalculationService`: landed cost = base + freight + customs duty (UK Trade Tariff via `HMRCTariffService`; falls back to `DEFAULT_CUSTOMS_DUTY_RATE`).
- `PriceRecommendationService`: `calculate_recommended_price`, `propose_markdown`, `propose_decay_markdowns` (nightly). It **only proposes**: it writes `PriceChangeLog` requests.
- **`PriceChangeLog` is a request.** A staff approval (409 if the price moved meanwhile) applies it to the product; only then does `pricing.tasks.sync_approved_prices` push it to Shopify. Never push to Shopify directly from a proposal.
- `apply_decay_pricing` must stay idempotent: markdowns are computed from the pre-markdown price stored in `PriceChangeLog`. One batch per product (nearest to expiry) drives its price.
- Money is `Decimal`; the frontend works in pence.

## Shopify (`common/services/shopify_service.py`, `sync/`)
- GraphQL Admin API only (REST product/variant endpoints are deprecated); keep `SHOPIFY_API_VERSION` supported.
- `ShopifyConnection.shop_domain` must match `*.myshopify.com` (SSRF guard). `access_token` is an `EncryptedTextField` and cannot be filtered on. One connection per company; only staff may change it.
- Only mocked tests exist for Shopify.

## Demand and calendar (`demand/`, `demand_calendar/`, `common/services/hijri_calendar_service.py`)
- Demand alerts: `common/tasks.py` raises "stock up" alerts for events starting within `DEMAND_ALERT_LEAD_DAYS` (default 30); `DEMAND_HISTORY_DAYS` (default 30) sets the sales history window; open alerts are listed at `GET /common/api/alerts/demand/` and dismissed with `POST .../dismiss/<id>/`.
- `CulturalEvent`/calendar are shared reference data (staff-write); Hijri dates come from AlAdhan.
- `seed_cultural_events` loads placeholder events and is idempotent.

## Freight (`logistics/`)
`FreightAlert`, providers, deliveries and cached rates (`FreightRateCache`, `FREIGHT_RATES_API_URL`, `RATE_CHANGE_THRESHOLD`).

## Tasks
Celery schedules live in `config/celery.py`; each task must tolerate being re-run. Test services with mocked HTTP and `pytest -k <name>`.
