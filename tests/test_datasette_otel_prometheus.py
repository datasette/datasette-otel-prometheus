import asyncio
import contextlib
import os
import socket
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request

import pytest
from datasette.app import Datasette
from datasette.utils import StartupError
from opentelemetry import metrics

from datasette_otel_prometheus import PluginConfig


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


def run_fresh(script, **env):
    """
    Run a script in a new interpreter: OpenTelemetry's meter provider is
    set-once per process, so anything about what happens at plugin import
    needs a process of its own.
    """
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env},
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
    return result


def test_proxy_meter_rebinds_to_late_installed_provider():
    # Instruments created through the API's proxy BEFORE the plugin module
    # imports re-bind to the plugin's provider
    run_fresh(
        """
        from opentelemetry import metrics

        meter = metrics.get_meter("test-proxy")
        counter = meter.create_counter("proxy_early")
        counter.add(1)  # dropped: no provider yet

        import datasette_otel_prometheus  # import installs the provider
        from prometheus_client import generate_latest

        counter.add(5)
        body = generate_latest(datasette_otel_prometheus._registry).decode()
        line = next(
            l for l in body.splitlines() if l.startswith("proxy_early_total")
        )
        assert line.endswith(" 5.0"), line
        print("OK")
        """
    )


def test_config_defaults():
    config = PluginConfig()
    assert (config.host, config.port) == ("127.0.0.1", None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plugin_config,expected",
    [
        ({"prot": 9100}, "prot: Extra inputs are not permitted"),
        ({"port": "nope"}, "port: Input should be a valid integer"),
        ({"port": 0}, "port: Input should be greater than or equal to 1"),
        ({"port": 70000}, "port: Input should be less than or equal to 65535"),
        ({"host": 5}, "host: Input should be a valid string"),
    ],
)
async def test_invalid_config_raises_startup_error(plugin_config, expected):
    with pytest.raises(StartupError) as excinfo:
        await make_datasette(plugin_config)
    message = str(excinfo.value)
    assert message.startswith("Invalid datasette-otel-prometheus plugin config")
    assert expected in message


@pytest.mark.asyncio
async def test_no_listener_without_port():
    datasette = await make_datasette()
    await datasette.start_background_tasks()
    assert not [
        t
        for t in datasette._background_tasks.tasks()
        if t.name.startswith("datasette-otel-prometheus")
    ]
    await datasette.invoke_shutdown()


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


SERVICE_NAME_SCRIPT = """
    from opentelemetry import metrics
    import datasette_otel_prometheus
    from prometheus_client import generate_latest

    # target_info only renders once at least one metric exists
    metrics.get_meter("probe").create_counter("probe").add(1)
    body = generate_latest(datasette_otel_prometheus._registry).decode()
    line = next(l for l in body.splitlines() if l.startswith("target_info"))
    assert 'service_name="{expected}"' in line, line
    print("OK")
"""


def test_service_name_defaults_to_datasette():
    env = {"OTEL_SERVICE_NAME": "", "OTEL_RESOURCE_ATTRIBUTES": ""}
    run_fresh(SERVICE_NAME_SCRIPT.format(expected="datasette"), **env)


def test_otel_service_name_env():
    run_fresh(
        SERVICE_NAME_SCRIPT.format(expected="from-env"),
        OTEL_SERVICE_NAME="from-env",
    )


def test_otel_resource_attributes_service_name():
    run_fresh(
        SERVICE_NAME_SCRIPT.format(expected="from-attrs"),
        OTEL_SERVICE_NAME="",
        OTEL_RESOURCE_ATTRIBUTES="service.name=from-attrs",
    )


def test_foreign_provider_serves_empty_registry():
    result = run_fresh(
        """
        from opentelemetry import metrics
        from opentelemetry.sdk.metrics import MeterProvider

        metrics.set_meter_provider(MeterProvider())
        metrics.get_meter("theirs").create_counter("theirs").add(1)

        import datasette_otel_prometheus
        from prometheus_client import generate_latest

        body = generate_latest(datasette_otel_prometheus._registry).decode()
        assert "theirs" not in body, body
        print("OK")
        """
    )
    assert "already installed" in result.stderr
