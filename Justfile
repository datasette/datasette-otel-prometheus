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

# Datasette on port 8002, with metrics on the plugin's own listener at
# 127.0.0.1:9464/metrics
dev *options: demo-db
    OTEL_SERVICE_NAME=demo-datasette uv run datasette demo.db \
        -s plugins.datasette-otel-prometheus.port 9464 \
        -p 8002 {{ options }}

# Scrape the metrics listener the way Prometheus would
scrape:
    curl -s http://localhost:9464/metrics
