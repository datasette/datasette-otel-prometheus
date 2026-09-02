# datasette resolves through the [tool.uv.sources] override in pyproject.toml,
# which pins the asg017/otel-phase1-6-plugin-kit branch of simonw/datasette.
# That branch carries core's own datasette_* / db_client_* metrics, so `dev`
# shows them at /-/metrics without any local checkout.

default:
    @just --list --unsorted

# Run the test suite
test *options:
    uv run pytest {{ options }}

# Generate demo.db (200-row table) if missing
demo-db:
    @[ -e demo.db ] || sqlite3 demo.db "create table plants(id integer primary key, name text, height_cm real); with recursive n(i) as (select 1 union all select i + 1 from n where i < 200) insert into plants select i, 'plant ' || i, abs(random() % 300) from n;"

# Datasette with the plugin serving /-/metrics on port 8002
dev *options: demo-db
    uv run datasette demo.db \
        -s plugins.datasette-otel-prometheus.service_name demo-datasette \
        -p 8002 {{ options }}

# Scrape the endpoint the way Prometheus would
scrape:
    curl -s http://localhost:8002/-/metrics
