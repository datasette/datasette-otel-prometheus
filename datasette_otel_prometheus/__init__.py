"""
Serve Datasette's OpenTelemetry metrics to Prometheus at host:port/metrics
(host defaults to 127.0.0.1), started as a Datasette background task. No
listener starts unless ``port`` is configured.

The MeterProvider is installed at import, since recordings made before one
exists are dropped. If another provider got there first (e.g. under
opentelemetry-instrument), we can't join it, so we serve an empty registry.
"""

import asyncio
import os
import sys

from datasette import hookimpl
from opentelemetry import metrics
from opentelemetry.attributes import BoundedAttributes
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.metrics._internal import _ProxyMeterProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from prometheus_client import CollectorRegistry, start_http_server

PLUGIN_NAME = "datasette-otel-prometheus"
DEFAULT_SERVICE_NAME = "datasette"
DEFAULT_HOST = "127.0.0.1"

# Module state, rebuilt by _install(). "mode" is one of:
#   "owner"   - our provider is the global one; metrics flow into our registry
#   "foreign" - someone else installed a provider first; we serve an empty
#               registry and cannot see their metrics
_state = {}


def _log(message):
    print(f"{PLUGIN_NAME}: {message}", file=sys.stderr)


def _install():
    "Runs at module import; re-runnable by tests after resetting otel globals."
    _state.clear()
    registry = CollectorRegistry()
    existing = metrics.get_meter_provider()
    if not isinstance(existing, (_ProxyMeterProvider, NoOpMeterProvider)):
        _log(
            "a MeterProvider is already installed "
            "(running under opentelemetry-instrument?) - metric readers are "
            "constructor-only, so its metrics cannot be served here"
        )
        _state.update(mode="foreign", registry=registry, resource=None)
        return

    resource_attributes = {}
    if "OTEL_SERVICE_NAME" not in os.environ:
        resource_attributes["service.name"] = DEFAULT_SERVICE_NAME
    resource = Resource.create(resource_attributes)

    reader = PrometheusMetricReader(registry=registry)
    provider = MeterProvider(metric_readers=[reader], resource=resource)
    metrics.set_meter_provider(provider)

    _state.update(
        mode="owner",
        registry=registry,
        provider=provider,
        reader=reader,
        resource=resource,
    )


def _set_service_name(resource, service_name):
    # Resource is immutable by design, but the reader consults this exact
    # object at collect time - replacing its attribute mapping lands the
    # configured name in target_info without rebuilding the provider.
    attributes = dict(resource.attributes)
    attributes["service.name"] = service_name
    resource._attributes = BoundedAttributes(attributes=attributes, immutable=True)
    # The exporter's collector caches target_info at its first collect, so a
    # swap after any scrape would never land - drop the cache (private attr,
    # so best-effort: worst case an early scrape keeps the default name).
    try:
        _state["reader"]._collector._target_info = None
    except (KeyError, AttributeError):
        pass


def _plugin_config(datasette):
    return datasette.plugin_config(PLUGIN_NAME) or {}


def _listen_address(config):
    "(host, port) for the listener, or None when no port is configured."
    if config.get("port") is None:
        return None
    return str(config.get("host") or DEFAULT_HOST), int(config["port"])


async def _serve_metrics_port(host, port):
    try:
        server, _thread = start_http_server(
            port, addr=host, registry=_state["registry"]
        )
    except OSError as e:
        _log(f"could not listen on {host}:{port} - {e}")
        raise
    try:
        # The thread does the serving; this task only holds the listener
        # open until Datasette cancels it at shutdown
        await asyncio.Event().wait()
    finally:
        # shutdown() blocks until serve_forever() notices, up to its 0.5s
        # poll interval - keep that off the event loop
        await asyncio.to_thread(server.shutdown)
        server.server_close()


_install()


@hookimpl
def startup(datasette):
    config = _plugin_config(datasette)
    if (
        _state["mode"] == "owner"
        and config.get("service_name")
        and "OTEL_SERVICE_NAME" not in os.environ
    ):
        _set_service_name(_state["resource"], str(config["service_name"]))

    address = _listen_address(config)
    if address is None:
        return
    host, port = address

    async def metrics_server(datasette):
        await _serve_metrics_port(host, port)

    datasette.add_background_task(metrics_server, name=f"{PLUGIN_NAME} server")
