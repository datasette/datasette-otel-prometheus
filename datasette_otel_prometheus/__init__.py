"""
Serve Datasette's OpenTelemetry metrics to Prometheus at host:port/metrics
(host defaults to 127.0.0.1), started as a Datasette background task. No
listener starts unless ``port`` is configured.

The MeterProvider is installed at import, since recordings made before one
exists are dropped. Set the service name with OTEL_SERVICE_NAME (default
"datasette"). If another provider got there first (e.g. under
opentelemetry-instrument), we can't join it, so we serve an empty registry.
"""

import asyncio
import sys

from datasette import hookimpl
from datasette.utils import StartupError
from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.metrics._internal import _ProxyMeterProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from prometheus_client import CollectorRegistry, start_http_server
from pydantic import BaseModel, ConfigDict, Field, ValidationError

PLUGIN_NAME = "datasette-otel-prometheus"
DEFAULT_SERVICE_NAME = "datasette"


class PluginConfig(BaseModel):
    # Unknown keys are almost always typos ("prot"), which would otherwise
    # silently leave the listener off
    model_config = ConfigDict(extra="forbid")

    port: int | None = Field(default=None, ge=1, le=65535)
    host: str = "127.0.0.1"


def _log(message):
    print(f"{PLUGIN_NAME}: {message}", file=sys.stderr)


_registry = CollectorRegistry()

if isinstance(metrics.get_meter_provider(), (_ProxyMeterProvider, NoOpMeterProvider)):
    # Honour OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES, and only fall back
    # to "datasette" over the SDK's "unknown_service" placeholder
    resource = Resource.create()
    if str(resource.attributes.get("service.name", "")).startswith("unknown_service"):
        resource = resource.merge(Resource({"service.name": DEFAULT_SERVICE_NAME}))
    metrics.set_meter_provider(
        MeterProvider(
            metric_readers=[PrometheusMetricReader(registry=_registry)],
            resource=resource,
        )
    )
else:
    _log(
        "a MeterProvider is already installed "
        "(running under opentelemetry-instrument?) - metric readers are "
        "constructor-only, so its metrics cannot be served here"
    )


def _plugin_config(datasette):
    try:
        return PluginConfig.model_validate(datasette.plugin_config(PLUGIN_NAME) or {})
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(map(str, err['loc'])) or 'config'}: {err['msg']}"
            for err in e.errors()
        )
        raise StartupError(f"Invalid {PLUGIN_NAME} plugin config - {problems}")


async def _serve_metrics_port(host, port):
    try:
        server, _thread = start_http_server(port, addr=host, registry=_registry)
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


@hookimpl
def startup(datasette):
    config = _plugin_config(datasette)
    if config.port is None:
        return

    async def metrics_server(datasette):
        await _serve_metrics_port(config.host, config.port)

    datasette.add_background_task(metrics_server, name=f"{PLUGIN_NAME} server")
