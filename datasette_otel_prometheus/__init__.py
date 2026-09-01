"""
Serve Datasette's OpenTelemetry metrics at /-/metrics for Prometheus scraping.

Datasette core (phase 3) and other plugins record metrics through the
OpenTelemetry metrics API, but without a MeterProvider every recording is a
no-op. This plugin installs an SDK ``MeterProvider`` whose
``PrometheusMetricReader`` renders everything into a dedicated
``prometheus_client`` registry, and serves that registry in Prometheus text
format from a ``register_routes()`` endpoint. The reader collects on scrape,
so the exposition is always current and there is no export interval.

The provider is installed at module import: the API's ``_ProxyMeterProvider``
re-binds meters and instruments created before ``set_meter_provider()``, but
recordings made before a provider exists are dropped, so earlier is better.

If some other machinery (the ``opentelemetry-instrument`` agent) already
installed a MeterProvider, this plugin announces itself once on stderr and
serves its own - then empty - registry rather than 404ing: SDK metric readers
are constructor-only, so a foreign provider cannot be joined after the fact.
"""

import os
import re
import sys

from datasette import hookimpl, Response
from opentelemetry import metrics
from opentelemetry.attributes import BoundedAttributes
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.metrics._internal import _ProxyMeterProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    generate_latest,
)

PLUGIN_NAME = "datasette-otel-prometheus"
DEFAULT_SERVICE_NAME = "datasette"
DEFAULT_PATH = "/-/metrics"

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


@hookimpl
def register_routes(datasette):
    config = _plugin_config(datasette)
    path = str(config.get("path") or DEFAULT_PATH)
    if not path.startswith("/"):
        path = "/" + path

    async def serve_metrics(request, datasette):
        if _plugin_config(datasette).get("actor_required") and request.actor is None:
            return Response.text("Forbidden", status=403)
        body = generate_latest(_state["registry"])
        return Response(
            body.decode("utf-8"),
            content_type=CONTENT_TYPE_LATEST,
        )

    return [(r"^" + re.escape(path) + r"$", serve_metrics)]
