# Unlike the sibling trace plugins, this one does not need the editable
# ~/projects/datasette checkout: it serves whatever is recorded through the
# OTel metrics API, and the hooks it uses (register_routes, startup, plugin
# config) all exist in the published 1.0 alphas. Core's own metrics are phase
# 3, in progress on the asg017/otel-metrics-phase3 branch - use `test-core`
# to run against that checkout once it stabilizes.

default:
    @just --list --unsorted

# Run the test suite against PyPI's datasette 1.0 alpha
test *options:
    uv run --isolated \
      --with-editable . \
      --with pytest --with pytest-asyncio \
      pytest {{ options }}

# Same, against the editable core checkout (phase-3 metrics integration)
test-core *options:
    uv run --no-project --isolated \
      --with-editable . \
      --with-editable ~/projects/datasette \
      --with pytest --with pytest-asyncio \
      pytest {{ options }}

# Generate demo.db (200-row table) if missing
demo-db:
    @[ -e demo.db ] || sqlite3 demo.db "create table plants(id integer primary key, name text, height_cm real); with recursive n(i) as (select 1 union all select i + 1 from n where i < 200) insert into plants select i, 'plant ' || i, abs(random() % 300) from n;"

# Datasette with the plugin serving /-/metrics on port 8002
dev *options: demo-db
    uv run --isolated \
      --with-editable . \
      datasette demo.db \
        -s plugins.datasette-otel-prometheus.service_name demo-datasette \
        -p 8002 {{ options }}

# Same, against the editable core checkout - the only variant that shows
# core's phase-3 datasette_* / db_client_* metrics (plain `dev` serves PyPI's
# alpha, which emits none, so /-/metrics stays empty until something records)
dev-core *options: demo-db
    uv run --no-project --isolated \
      --with-editable . \
      --with-editable ~/projects/datasette \
      datasette demo.db \
        -s plugins.datasette-otel-prometheus.service_name demo-datasette \
        -p 8002 {{ options }}

# Scrape the endpoint the way Prometheus would
scrape:
    curl -s http://localhost:8002/-/metrics
