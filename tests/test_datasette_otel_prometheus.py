import subprocess
import sys
import textwrap

import pytest
from datasette.app import Datasette
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider

import datasette_otel_prometheus
from conftest import reset_meter_state


async def make_datasette(plugin_config=None):
    datasette = Datasette(
        memory=True,
        config={"plugins": {"datasette-otel-prometheus": plugin_config or {}}},
    )
    await datasette.invoke_startup()
    return datasette


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

    datasette = await make_datasette()
    response = await datasette.client.get("/-/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=")
    body = response.text
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
        [sys.executable, "-c", script], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


@pytest.mark.asyncio
async def test_path_config_moves_the_endpoint():
    datasette = await make_datasette({"path": "/metrics"})
    assert (await datasette.client.get("/metrics")).status_code == 200
    assert (await datasette.client.get("/-/metrics")).status_code == 404


@pytest.mark.asyncio
async def test_actor_required():
    datasette = await make_datasette({"actor_required": True})
    anonymous = await datasette.client.get("/-/metrics")
    assert anonymous.status_code == 403
    cookie = datasette.sign({"a": {"id": "root"}}, "actor")
    signed_in = await datasette.client.get(
        "/-/metrics", cookies={"ds_actor": cookie}
    )
    assert signed_in.status_code == 200


@pytest.mark.asyncio
async def test_service_name_config_lands_in_target_info():
    # target_info only renders once at least one metric exists
    metrics.get_meter("test-service-name").create_counter("svc_probe").add(1)
    datasette = await make_datasette({"service_name": "my-datasette"})
    response = await datasette.client.get("/-/metrics")
    assert 'service_name="my-datasette"' in response.text
    assert "target_info" in response.text


@pytest.mark.asyncio
async def test_otel_service_name_env_beats_config(monkeypatch):
    monkeypatch.setenv("OTEL_SERVICE_NAME", "from-env")
    reset_meter_state()
    datasette_otel_prometheus._install()
    datasette = await make_datasette({"service_name": "from-config"})
    resource = datasette_otel_prometheus._state["resource"]
    assert resource.attributes["service.name"] == "from-env"


@pytest.mark.asyncio
async def test_foreign_provider_serves_empty_but_wellformed(capsys):
    reset_meter_state()
    metrics.set_meter_provider(MeterProvider())
    datasette_otel_prometheus._install()
    assert datasette_otel_prometheus._state["mode"] == "foreign"
    assert "already installed" in capsys.readouterr().err

    datasette = await make_datasette()
    response = await datasette.client.get("/-/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=")
