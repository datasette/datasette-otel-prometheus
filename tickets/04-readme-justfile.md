# 04 — README + Justfile

Status: done

README must contain: quickstart (install → scrape), the Fly.io story
(`fly.toml` `[metrics]` block, fly-metrics.net dashboards, org Prometheus API),
config reference, what metrics exist today vs. with core phase 3, the
default-open tradeoff (metric names/labels can reveal query patterns), and the
foreign-provider caveat.

Justfile mirrors the otlp plugin: `just test` (uv, `--no-project --isolated`,
editable datasette), `just dev` serving demo.db with the plugin, `just scrape`
curling the endpoint.

## Acceptance

- `just dev` + `just scrape` shows a live exposition including `target_info`.
