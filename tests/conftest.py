"""
Metrics globals (metrics.set_meter_provider) are set-once per process, same as
the tracer globals the sibling trace plugins wrestle with. This conftest
snapshots the provider the plugin installed at import; reset_otel restores it
between tests, and reset_meter_state() unwinds the set-once global entirely
for foreign-provider tests that need to re-run _install().
"""

import pytest
from opentelemetry import metrics
from opentelemetry.attributes import BoundedAttributes

import datasette_otel_prometheus

_SNAPSHOT = dict(datasette_otel_prometheus._state)
_SNAPSHOT_RESOURCE_ATTRIBUTES = dict(_SNAPSHOT["resource"].attributes)


def reset_meter_state():
    "Unwind OpenTelemetry's set-once meter provider global."
    metrics._internal._METER_PROVIDER = None
    metrics._internal._METER_PROVIDER_SET_ONCE._done = False


@pytest.fixture(autouse=True)
def reset_otel():
    metrics._internal._METER_PROVIDER = _SNAPSHOT["provider"]
    metrics._internal._METER_PROVIDER_SET_ONCE._done = True
    state = datasette_otel_prometheus._state
    state.clear()
    state.update(_SNAPSHOT)
    state["resource"]._attributes = BoundedAttributes(
        attributes=_SNAPSHOT_RESOURCE_ATTRIBUTES, immutable=True
    )
    yield
