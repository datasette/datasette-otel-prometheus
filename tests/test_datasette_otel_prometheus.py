import asyncio
import contextlib
import socket
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request

import pytest
from datasette.app import Datasette
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider

import datasette_otel_prometheus
from conftest import reset_meter_state


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def scrape(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as r:
        return r.status, r.headers["content-type"], r.read().decode()


async def make_datasette(plugin_config=None):
    datasette = Datasette(
        memory=True,
        config={"plugins": {"datasette-otel-prometheus": plugin_config or {}}},
    )
    await datasette.invoke_startup()
    return datasette


@contextlib.asynccontextmanager
async def serving(plugin_config=None):
    "A Datasette with its metrics listener up on a free port; yields the port."
    port = free_port()
    datasette = await make_datasette({"port": port, **(plugin_config or {})})
    await datasette.start_background_tasks()
    await asyncio.sleep(0)  # let the task bind
    try:
        yield port
    finally:
        await datasette.invoke_shutdown()


@pytest.mark.asyncio
async def test_plugin_is_installed():
    datasette = await make_datasette()
    response = await datasette.client.get("/-/plugins.json")
    assert response.status_code == 200
    installed = [plugin["name"] for plugin in response.json()]
    assert "datasette-otel-prometheus" in installed


@pytest.mark.asyncio
async def test_counter_and_histogram_appear_in_exposition():
    meter = metrics.get_meter("test-e2e")
    counter = meter.create_counter("e2e_requests", unit="1")
    counter.add(3, {"route": "/demo"})
    histogram = meter.create_histogram("e2e_query_seconds", unit="s")
    histogram.record(0.25)

    async with serving() as port:
        # A blocking call on the event loop: the scrape still answers because
        # the listener runs in its own thread
        status, content_type, body = scrape(port)
    assert status == 200
    assert content_type.startswith("text/plain; version=")
    # Series carry otel_scope_* labels alongside recorded attributes, so
    # match the pieces rather than one exact line
    counter_line = next(
        line for line in body.splitlines() if line.startswith("e2e_requests_total{")
    )
    assert 'route="/demo"' in counter_line
    assert counter_line.endswith(" 3.0")
    assert "e2e_query_seconds_bucket" in body
    assert "target_info" in body


def test_proxy_meter_rebinds_to_late_installed_provider():
    # The import-time-install claim: instruments created through the API's
    # proxy BEFORE the plugin module imports re-bind to the plugin's provider.
    # Must run in a clean subprocess - in this process the API's singleton
    # proxy is permanently bound to the first provider ever installed (the
    # conftest snapshot), so recordings would land in that provider's
    # registry no matter what a later _install() does.
    script = textwrap.dedent(
        """
        from opentelemetry import metrics

        meter = metrics.get_meter("test-proxy")
        counter = meter.create_counter("proxy_early")
        counter.add(1)  # dropped: no provider yet

        import datasette_otel_prometheus  # import installs the provider
        from prometheus_client import generate_latest

        counter.add(5)
        body = generate_latest(
            datasette_otel_prometheus._state["registry"]
        ).decode()
        line = next(
            l for l in body.splitlines() if l.startswith("proxy_early_total")
        )
        assert line.endswith(" 5.0"), line
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_listen_address_defaults():
    assert datasette_otel_prometheus._listen_address({}) == ("127.0.0.1", 9464)
    assert datasette_otel_prometheus._listen_address(
        {"host": "0.0.0.0", "port": "9100"}
    ) == ("0.0.0.0", 9100)


@pytest.mark.asyncio
async def test_no_metrics_route_on_datasette_port():
    datasette = await make_datasette()
    assert (await datasette.client.get("/-/metrics")).status_code == 404


@pytest.mark.asyncio
async def test_port_not_bound_until_background_tasks_launch():
    port = free_port()
    datasette = await make_datasette({"port": port})
    # invoke_startup alone (the --get / inspect shape) must not bind
    with pytest.raises(urllib.error.URLError):
        scrape(port)
    await datasette.invoke_shutdown()


@pytest.mark.asyncio
async def test_listener_closes_on_shutdown():
    async with serving() as port:
        assert scrape(port)[0] == 200
    with pytest.raises(urllib.error.URLError):
        scrape(port)


@pytest.mark.asyncio
async def test_port_in_use_crashes_task_with_clear_message(capsys):
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        port = blocker.getsockname()[1]
        datasette = await make_datasette({"port": port})
        await datasette.start_background_tasks()
        await asyncio.sleep(0.05)
        (task,) = [
            t
            for t in datasette._background_tasks.tasks()
            if t.name.startswith("datasette-otel-prometheus")
        ]
        assert task.state == "crashed"
        assert isinstance(task.exception, OSError)
        assert f"could not listen on 127.0.0.1:{port}" in capsys.readouterr().err
        await datasette.invoke_shutdown()


@pytest.mark.asyncio
async def test_service_name_config_lands_in_target_info():
    # target_info only renders once at least one metric exists
    metrics.get_meter("test-service-name").create_counter("svc_probe").add(1)
    async with serving({"service_name": "my-datasette"}) as port:
        body = scrape(port)[2]
    assert 'service_name="my-datasette"' in body
    assert "target_info" in body


@pytest.mark.asyncio
async def test_otel_service_name_env_beats_config(monkeypatch):
    monkeypatch.setenv("OTEL_SERVICE_NAME", "from-env")
    reset_meter_state()
    datasette_otel_prometheus._install()
    await make_datasette({"service_name": "from-config"})
    resource = datasette_otel_prometheus._state["resource"]
    assert resource.attributes["service.name"] == "from-env"


@pytest.mark.asyncio
async def test_foreign_provider_serves_empty_but_wellformed(capsys):
    reset_meter_state()
    metrics.set_meter_provider(MeterProvider())
    datasette_otel_prometheus._install()
    assert datasette_otel_prometheus._state["mode"] == "foreign"
    assert "already installed" in capsys.readouterr().err

    async with serving() as port:
        status, content_type, _body = scrape(port)
    assert status == 200
    assert content_type.startswith("text/plain; version=")
