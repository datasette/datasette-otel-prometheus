# 01 — Scaffold

Status: done

Package skeleton in the house style (copied from datasette-otel-otlp): setuptools
backend, `Framework :: Datasette` classifier, Apache-2.0, entry point
`otel_prometheus = "datasette_otel_prometheus"`.

Dependencies: `datasette>=1a37`, `opentelemetry-sdk>=1.37`,
`opentelemetry-exporter-prometheus>=0.58b0`, `prometheus-client` (imported
directly for `generate_latest`). Tested against opentelemetry-sdk 1.44.0,
opentelemetry-exporter-prometheus 0.65b0, prometheus_client 0.26.0.

## Acceptance

- `just test` runs; plugin appears in `/-/plugins`.
