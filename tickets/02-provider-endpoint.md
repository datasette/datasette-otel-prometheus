# 02 — MeterProvider + /-/metrics endpoint

Status: done

Measured findings (opentelemetry-exporter-prometheus 0.65b0, prometheus_client
0.26.0):

- Series carry `otel_scope_name`/`otel_scope_schema_url`/`otel_scope_version`
  labels (scope_info_enabled default) alongside recorded attributes.
- `prometheus_client` 0.26's `CONTENT_TYPE_LATEST` is
  `text/plain; version=1.0.0` (was 0.0.4).
- The exporter's `_CustomCollector` renders **nothing** — not even
  `target_info` — until at least one metric has been recorded, and it caches
  `target_info` at its first collect. `_set_service_name` therefore drops that
  cache (private attr, try/except-guarded) so a post-scrape config swap still
  lands.

## Provider wiring

At module import, `_install()`:

- `metrics.get_meter_provider()` is the API's `_ProxyMeterProvider` or
  `NoOpMeterProvider` → build a dedicated `CollectorRegistry`, a
  `PrometheusMetricReader(registry=...)`, an SDK
  `MeterProvider(metric_readers=[reader], resource=...)`, and
  `metrics.set_meter_provider(...)`. `service.name` defaults to `"datasette"`
  unless `OTEL_SERVICE_NAME` is set (mode `"owner"`).
- Anything else already installed (agent) → mode `"foreign"`: one stderr line.
  SDK metric readers are constructor-only, so the foreign provider cannot be
  joined; the endpoint still serves the plugin's (empty) registry rather than
  404ing, so scrapers keep getting a well-formed page. Measured on sdk 1.44:
  `MeterProvider` exposes no post-construction reader hook
  (`_all_metric_readers` is internal and readers need `_set_collect_callback`
  wiring done in the constructor).

## Endpoint

`register_routes()` serves `path` (default `/-/metrics`, configurable, must
start with `/`):

- Body: `prometheus_client.generate_latest(registry)` — the reader collects on
  scrape, so values are always current.
- Content type: `CONTENT_TYPE_LATEST` (`text/plain; version=0.0.4`).
- `actor_required: true` → 403 for requests with no actor. Default open;
  README documents the tradeoff.
- Route registered via an escaped literal path regex, not a wildcard.

## Config (startup hook)

- `path`, `actor_required` as above.
- `service_name`: applied by swapping the Resource's `BoundedAttributes`
  (the reader reads the provider's Resource at collect time → lands in
  `target_info`), same trick as datasette-otel-otlp, owner mode only,
  env `OTEL_SERVICE_NAME` wins.

## Acceptance

- A counter recorded through `metrics.get_meter(...)` appears at `/-/metrics`
  in Prometheus text format with its attributes as labels.
- `target_info` carries the configured `service_name`.
- Foreign provider → endpoint serves, stderr explains, nothing crashes.
