# datasette-otel-prometheus

Expose the OpenTelemetry metrics a Datasette process records at `/-/metrics` in
Prometheus text format, so any Prometheus-compatible scraper can collect them. The
motivating deployment is Fly.io's free managed observability: a three-line
`[metrics]` block in `fly.toml` and Fly scrapes the endpoint every 15 seconds into
its hosted Prometheus + Grafana (`fly-metrics.net`) — no collector, no vendor
account.

## Context

Datasette's phase-1 OTel stack ships traces only; core metrics (SQL thread queue
depth, query duration histograms) are phase 3 of the upstream roadmap and are being
assembled on the datasette branch `asg017/otel-metrics-phase3`. This plugin is the
serving half: it installs a `MeterProvider` whose `PrometheusMetricReader` renders
everything recorded through the OpenTelemetry metrics API — core's phase-3 metrics
when they exist, and any metrics other plugins record, today.

Sibling plugins: `datasette-otel-otlp` (spans → OTLP) and `datasette-otel-parquet`
(spans → Parquet). Metrics globals (`metrics.set_meter_provider`) are entirely
separate from tracer globals, so this plugin needs none of the tracer-provider
coexistence machinery those two share.

## Design decisions (made up front)

- **Provider installed at module import**, mirroring the trace plugins' reasoning:
  the API's `_ProxyMeterProvider` forwards meters created before
  `set_meter_provider()`, and instruments created through proxy meters re-bind to
  the real provider — but installing at import keeps the window where recordings
  are dropped as small as possible.
- **Dedicated `CollectorRegistry`**, not `prometheus_client`'s global default:
  `/-/metrics` serves OTel-derived metrics (plus `target_info`) only — no
  `python_gc_*` / `process_*` noise, and tests don't fight global registry state.
  Fly collects instance CPU/memory platform-side anyway.
- **Pull model end to end**: `PrometheusMetricReader` collects on scrape, so the
  endpoint is always fresh and there is no export interval to configure.
- **Foreign `MeterProvider` (agent user) → degraded, loudly.** SDK metric readers
  are constructor-only, so a provider someone else installed cannot be joined. The
  endpoint still serves (an agent configured with its own Prometheus reader may
  share our registry via env), but OTel metrics flowing to the foreign provider
  won't appear; one stderr line says so.
- **Default open, documented.** `/-/metrics` has no auth by default (Prometheus
  scrapers don't log in). Config `actor_required: true` returns 403 for
  actor-less requests; the README states the leak surface (metric names and label
  values can reveal query patterns).
- **`service_name`** config applies via the same Resource-attribute swap the otlp
  plugin proved out (the reader reads the provider's Resource at collect time, so
  a startup-hook swap lands in `target_info`).

## What it looks like to a user

```yaml
plugins:
  datasette-otel-prometheus:
    path: /-/metrics        # default
    service_name: my-datasette
    # actor_required: true  # 403 unless the request has an actor
```

```toml
# fly.toml — Fly's managed Prometheus scrapes it every 15s
[metrics]
  port = 8080
  path = "/-/metrics"
```

## Tickets

| # | Ticket | Status |
|---|--------|--------|
| 01 | [Scaffold](tickets/01-scaffold.md) | done |
| 02 | [MeterProvider + /-/metrics endpoint](tickets/02-provider-endpoint.md) | done |
| 03 | [Tests](tickets/03-tests.md) | done |
| 04 | [README + Justfile](tickets/04-readme-justfile.md) | done |

## Dev environment gotcha

Core emits no metrics on any branch yet (phase 3 in progress on
`asg017/otel-metrics-phase3`). The plugin is built and tested against its own test
meters; the integration smoke test against core's `datasette_*` metrics is pending
that branch. Demos run with `--with-editable ~/projects/datasette` per house style.
