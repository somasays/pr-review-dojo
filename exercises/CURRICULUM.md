# Curriculum

Thirty-two exercises. `/seed` builds every row whose order number is not yet
in `INDEX.md` or `SEED_LOG.md`. Order is the exercise number. Teach exercises
for a domain come before its test exercises, and domains alternate.

| order | mode | domain | difficulty | theme |
| --- | --- | --- | --- | --- |
| 1 | teach | fastapi | easy | Order notes: PATCH endpoint to add a free-text note to an order, with a notes field on the response |
| 2 | teach | logic | easy | Tiered volume discounts in domain pricing (buy 10 get 5 percent, buy 50 get 12 percent) |
| 3 | teach | services | easy | Order refund service method that reverses stock and sends a refund notification |
| 4 | teach | sqlalchemy | easy | Customer search endpoint by name prefix and region with a new repository query |
| 5 | test | fastapi | medium | Admin endpoint to list orders across customers with status filter and pagination |
| 6 | teach | spark_batch | easy | Weekly customer summary job built on top of daily_customer_orders |
| 7 | test | logic | medium | Date-range reporting helpers: fiscal quarters, business days, and range merging |
| 8 | teach | async | easy | Async notification batch dispatcher that drains pending confirmations |
| 9 | test | services | medium | Payment webhook handler that marks orders paid and reconciles amounts |
| 10 | teach | migrations | easy | Add shipped_at and tracking_number to orders with a migration |
| 11 | test | sqlalchemy | medium | Discount codes moved from a dict to a database table with usage limits |
| 12 | teach | rewrite:domain | rewrite | God function: a single compute_order_total that parses, validates, prices, and formats |
| 13 | teach | spark_streaming | easy | Stream: per-customer running count of paid orders written to a second sink |
| 14 | test | spark_batch | medium | Product-level daily aggregate joined to a products dimension parquet |
| 15 | teach | concurrency | easy | In-process API rate limiter keyed by API key |
| 16 | test | async | medium | Async stock sync worker that pulls supplier inventory and updates products |
| 17 | test | fastapi | hard | Customer self-service: address book endpoints with default address and order attachment |
| 18 | test | migrations | medium | Split customer name into first and last name with a backfill |
| 19 | test | logic | hard | Multi-currency money: exchange rates, conversion, and mixed-currency quotes |
| 20 | teach | rewrite:spark_batch | rewrite | Twelve chained withColumn calls in a daily enrichment job |
| 21 | test | services | hard | Order fulfillment orchestration: reserve stock, charge, ship, with compensation on failure |
| 22 | test | spark_streaming | medium | Stream: hourly windowed counts of order status changes with late data handling |
| 23 | test | sqlalchemy | hard | Order history export with cursor pagination, filters, and a raw SQL summary |
| 24 | test | concurrency | medium | Stock reservation cache with a background expiry thread |
| 25 | test | rewrite:services | rewrite | Service class with mixed responsibilities: config, HTTP calls, DB writes, and formatting in one |
| 26 | test | spark_batch | hard | Backfill mode for daily_orders with skew handling and a customers dimension join |
| 27 | test | async | hard | Async webhook fan-out with timeouts, retries, and graceful shutdown |
| 28 | test | migrations | hard | Introduce an order_events audit table and move status history into it |
| 29 | test | rewrite:spark_streaming | rewrite | Streaming job whose foreachBatch does parsing, dedupe, merge, metrics, and alerting inline |
| 30 | test | spark_streaming | hard | Stream: exactly-once merge into orders_latest plus a dead-letter sink for malformed events |
| 31 | test | concurrency | hard | Thread-safe notification flusher with batching and lock ordering across two queues |
| 32 | test | rewrite:fastapi | rewrite | API router with inline business logic: pricing, stock checks, and notifications inside handlers |
