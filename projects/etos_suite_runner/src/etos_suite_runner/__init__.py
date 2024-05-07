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

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_NAMESPACE, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from etos_lib.logging.logger import setup_logging

try:
    VERSION = version("etos_suite_runner")
except PackageNotFoundError:
    VERSION = "Unknown"


BASE_DIR = os.path.dirname(os.path.relpath(__file__))
DEV = os.getenv("DEV", "false").lower() == "true"
ENVIRONMENT = "development" if DEV else "production"
os.environ["ENVIRONMENT_PROVIDER_DISABLE_LOGGING"] = "true"
setup_logging("ETOS Suite Runner", VERSION, ENVIRONMENT)


LOGGER = logging.getLogger(__name__)

# Setting OTEL_COLLECTOR_HOST will override the default OTEL collector endpoint.
# This is needed because Suite Runner uses the cluster-level OpenTelemetry collector
# instead of a sidecar collector.
if os.getenv("OTEL_COLLECTOR_HOST"):
    os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = os.getenv("OTEL_COLLECTOR_HOST")
else:
    if "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" in os.environ:
        LOGGER.debug("Environment variable OTEL_EXPORTER_OTLP_TRACES_ENDPOINT not used.")
        LOGGER.debug("To specify an OpenTelemetry collector host use OTEL_COLLECTOR_HOST.")
        del os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"]

if os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
    LOGGER.info(
        "Using OpenTelemetry collector: %s", os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    )
    PROVIDER = TracerProvider(
        resource=Resource.create(
            {
                SERVICE_NAME: "etos-suite-runner",
                SERVICE_VERSION: VERSION,
                SERVICE_NAMESPACE: ENVIRONMENT,
            }
        )
    )
    EXPORTER = OTLPSpanExporter()
    PROCESSOR = BatchSpanProcessor(EXPORTER)
    PROVIDER.add_span_processor(PROCESSOR)
    trace.set_tracer_provider(PROVIDER)
else:
    LOGGER.info("OpenTelemetry not enabled. OTEL_COLLECTOR_HOST not set.")
