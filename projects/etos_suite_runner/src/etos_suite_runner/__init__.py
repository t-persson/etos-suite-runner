# Copyright 2020-2021 Axis Communications AB.
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
"""ETOS suite runner module."""

import logging
import os
from importlib.metadata import PackageNotFoundError, version

from etos_lib.logging.logger import setup_logging
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import (
    SERVICE_NAME,
    SERVICE_VERSION,
    OTELResourceDetector,
    ProcessResourceDetector,
    Resource,
    get_aggregated_resources,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from etos_suite_runner.lib.metrics import SuiteRunnerMetrics

try:
    VERSION = version("etos_suite_runner")
except PackageNotFoundError:
    VERSION = "Unknown"

DEV = os.getenv("DEV", "false").lower() == "true"
os.environ["ENVIRONMENT_PROVIDER_DISABLE_LOGGING"] = "true"

LOGGER = logging.getLogger(__name__)
meter_provider: MeterProvider | None = None

if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    OTEL_RESOURCE = Resource.create(
        {
            SERVICE_NAME: "etos-suite-runner",
            SERVICE_VERSION: VERSION,
            "etos.version": os.getenv("ETOS_VERSION") or "unknown",
        },
    )

    OTEL_RESOURCE = get_aggregated_resources(
        [OTELResourceDetector(), ProcessResourceDetector()],
    ).merge(OTEL_RESOURCE)
    LOGGER.info(
        "Using OpenTelemetry collector: %s",
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
    )
    PROVIDER = TracerProvider(resource=OTEL_RESOURCE)
    EXPORTER = OTLPSpanExporter()
    PROCESSOR = BatchSpanProcessor(EXPORTER)
    PROVIDER.add_span_processor(PROCESSOR)
    trace.set_tracer_provider(PROVIDER)
    METRIC_READER = PeriodicExportingMetricReader(OTLPMetricExporter())
    meter_provider = MeterProvider(
        resource=OTEL_RESOURCE,
        metric_readers=[METRIC_READER],
    )
    metrics.set_meter_provider(meter_provider)
    setup_logging("ETOS Suite Runner", VERSION, otel_resource=OTEL_RESOURCE)
else:
    setup_logging("ETOS Suite Runner", VERSION)
    LOGGER.info("OpenTelemetry not enabled. OTEL_EXPORTER_OTLP_ENDPOINT not set.")

METRICS = SuiteRunnerMetrics(metrics.get_meter("etos_suite_runner"))


def shutdown_metrics() -> None:
    """Flush and shut down OTLP metrics before this short-lived process exits."""
    if meter_provider is None:
        return
    try:
        meter_provider.force_flush()
    finally:
        meter_provider.shutdown()
