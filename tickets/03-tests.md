# 03 — Tests

Status: done

Standalone against the plugin's own test meters — core emits no metrics on any
branch yet (phase 3 pending on `asg017/otel-metrics-phase3`).

Cases:

1. End to end: counter + histogram recorded via `metrics.get_meter()` →
   `datasette.client.get("/-/metrics")` returns 200, Prometheus text format,
   correct content type, values and labels present.
2. Proxy forwarding: a meter created BEFORE the plugin imports rebinds (the
   import-time install claim). Runs in a clean **subprocess**: in the pytest
   process the API's singleton `_PROXY_METER_PROVIDER` is permanently bound to
   the first provider ever installed (the conftest snapshot), so recordings
   land in that provider's registry no matter what a later `_install()` does —
   the same test-process artifact the trace plugins' coexistence tests
   document for tracers.
3. `path` config moves the endpoint; default 404s.
4. `actor_required`: anonymous 403, actor 200 (via `ds.client` cookies /
   `actor` param pattern used in datasette tests).
5. `service_name` config lands in `target_info`; `OTEL_SERVICE_NAME` env wins.
6. Foreign provider: reset metrics globals, install an SDK provider first →
   plugin goes foreign, endpoint still 200s with a well-formed (possibly
   empty) exposition, one stderr line.
7. Pending (blocked on core phase 3): integration smoke asserting
   `datasette_*` metrics from core appear — see PLAN "Dev environment gotcha".

Bootstrap: metrics globals are set-once (`metrics._internal._METER_PROVIDER` /
`_METER_PROVIDER_SET_ONCE`), reset the same way the trace plugins' conftests
reset tracer globals.

## Acceptance

- `just test` green.
