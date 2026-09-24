default:
    @just --list --unsorted

# Run the test suite
test *options:
    uv run pytest {{ options }}

# Lint, format-check and type-check
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run ty check

# Auto-fix lint issues and reformat
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Generate demo.db (200-row table) if missing
demo-db:
    @[ -e demo.db ] || sqlite3 demo.db "create table plants(id integer primary key, name text, height_cm real); with recursive n(i) as (select 1 union all select i + 1 from n where i < 200) insert into plants select i, 'plant ' || i, abs(random() % 300) from n;"

# Datasette with the plugin serving /-/metrics on port 8002. The endpoint is
# deny-by-default, so grant the action to anonymous requests for the demo.
dev *options: demo-db
    uv run datasette demo.db \
        -s plugins.datasette-otel-prometheus.service_name demo-datasette \
        -s permissions.datasette-prometheus-metrics.unauthenticated true \
        -p 8002 {{ options }}

# Scrape the endpoint the way Prometheus would
scrape:
    curl -s http://localhost:8002/-/metrics
