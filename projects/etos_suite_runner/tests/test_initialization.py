# Copyright Axis Communications AB.
#
# For a full list of individual contributors, please see the commit history.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for Suite Runner version and runtime metric initialization."""

import runpy
from importlib.metadata import PackageNotFoundError
from unittest.mock import Mock

import pytest
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import SERVICE_VERSION

import etos_suite_runner


@pytest.fixture(name="isolated_initialization")
def initialize_module(monkeypatch):
    """Isolate module initialization from global providers and external exporters."""
    monkeypatch.delenv("BUILD_REVISION", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr("etos_lib.logging.logger.setup_logging", Mock())
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", Mock())
    monkeypatch.setattr("opentelemetry.metrics.set_meter_provider", Mock())
    monkeypatch.setattr("opentelemetry.sdk.trace.TracerProvider", Mock())
    monkeypatch.setattr("opentelemetry.sdk.trace.export.BatchSpanProcessor", Mock())
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter.OTLPSpanExporter", Mock()
    )
    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.grpc.metric_exporter.OTLPMetricExporter", Mock()
    )
    return lambda: runpy.run_path(etos_suite_runner.__file__)


@pytest.mark.parametrize(
    "revision,package_version,expected",
    [
        ("abc12345", "1.2.3", "abc12345"),
        ("abc12345", None, "abc12345"),
        (None, "1.2.3", "1.2.3"),
        ("", "1.2.3", "1.2.3"),
        (None, None, "Unknown"),
    ],
)
def test_version(isolated_initialization, monkeypatch, revision, package_version, expected):
    """Prefer the image revision while preserving package and unknown fallbacks."""
    if revision is not None:
        monkeypatch.setenv("BUILD_REVISION", revision)
    lookup = Mock(return_value=package_version)
    if package_version is None:
        lookup.side_effect = PackageNotFoundError
    monkeypatch.setattr("importlib.metadata.version", lookup)
    instrument = Mock()
    monkeypatch.setattr(SystemMetricsInstrumentor, "instrument", instrument)

    result = isolated_initialization()

    assert result["VERSION"] == expected
    instrument.assert_not_called()
    if revision:
        lookup.assert_not_called()


def test_default_runtime_metrics(isolated_initialization, monkeypatch):
    """Collect default system and process metrics with the image version resource."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    monkeypatch.setenv("BUILD_REVISION", "abc12345")
    reader = InMemoryMetricReader()
    monkeypatch.setattr(
        "opentelemetry.sdk.metrics.export.PeriodicExportingMetricReader",
        Mock(return_value=reader),
    )
    result = isolated_initialization()
    try:
        data = reader.get_metrics_data()
        names = {
            metric.name
            for resource_metrics in data.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
        }
        assert any(name.startswith("system.") for name in names)
        assert any(name.startswith("process.runtime.") for name in names)
        assert all(
            resource_metrics.resource.attributes[SERVICE_VERSION] == "abc12345"
            for resource_metrics in data.resource_metrics
        )
    finally:
        SystemMetricsInstrumentor().uninstrument()
        result["METER_PROVIDER"].shutdown()
