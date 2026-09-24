# datasette-otel-prometheus

[![PyPI](https://img.shields.io/pypi/v/datasette-otel-prometheus.svg)](https://pypi.org/project/datasette-otel-prometheus/)

Serve the OpenTelemetry metrics a Datasette process records at `/-/metrics`, in
Prometheus text format, for any Prometheus-compatible scraper. No collector, no
push pipeline — the endpoint collects on scrape and is always current.

## Quickstart

```bash
datasette install datasette-otel-prometheus
datasette mydb.db
curl http://localhost:8001/-/metrics
```

That's it. The plugin installs an OpenTelemetry `MeterProvider` at import time, so
every metric recorded through the OpenTelemetry metrics API — by Datasette core, or
by any other plugin — flows to the endpoint.

## The Fly.io story

Fly.io's free observability stack is exactly this shape: their managed Prometheus
scrapes an endpoint you declare in `fly.toml` every 15 seconds, and dashboards live
in their hosted Grafana at [fly-metrics.net](https://fly-metrics.net). The entire
Fly-side configuration:

```toml
[metrics]
  port = 8080          # your app's internal port
  path = "/-/metrics"
```

The same data is queryable from any external Grafana via Fly's org-scoped
Prometheus API (`https://api.fly.io/prometheus/<org>/`).

## What metrics exist today?

The endpoint serves whatever is recorded through the OpenTelemetry metrics API:

- **Today**: metrics recorded by other plugins, plus `target_info` (service
  metadata). Datasette core does not record metrics yet.
- **With core phase 3** (in progress): `datasette_*` metrics — SQL thread queue
  depth, query duration histograms, and friends. When that ships, they appear here
  with zero changes to this plugin.

## Configuration

```yaml
plugins:
  datasette-otel-prometheus:
    path: /-/metrics          # default
    service_name: my-datasette  # sets service_name in target_info; default "datasette"
```

`OTEL_SERVICE_NAME` in the environment beats the `service_name` setting.

## Access control

The endpoint is gated by the `datasette-prometheus-metrics` permission and is
**denied by default** — anyone without it, signed in or not, gets a plain-text
`403`. Metric names and label values can reveal usage patterns of your instance,
so grant it deliberately, the same way as any other Datasette action:

```yaml
permissions:
  datasette-prometheus-metrics:
    unauthenticated: true   # scraper on a private network, no login
```

or, for a scraper that authenticates (e.g. an API token from
`datasette-auth-tokens`, or a specific actor):

```yaml
permissions:
  datasette-prometheus-metrics:
    id: prometheus
```

`datasette --root` grants it to the root user like every other action. On Fly,
the `[metrics]` scrape happens over Fly's private network, so `unauthenticated:
true` is reasonable as long as your app only exposes the port internally.

## Running under `opentelemetry-instrument`

If an agent already installed a `MeterProvider`, this plugin cannot join it — SDK
metric readers are constructor-only. It says so once on stderr and serves its own
(empty) registry so scrapers still get a well-formed page. In that setup, configure
the agent's own Prometheus exporter instead.

## Development

```bash
just test    # test suite against PyPI's datasette 1.0 alpha
just dev     # serve demo.db with the plugin on :8002
just scrape  # curl the endpoint the way Prometheus would
```
