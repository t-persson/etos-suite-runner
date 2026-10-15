#!/usr/bin/env python
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
# -*- coding: utf-8 -*-
"""OpenTelemetry-related code."""

import logging
import os
import time
from contextlib import contextmanager

import opentelemetry
from opentelemetry import metrics, trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagators._envcarrier import EnvironmentGetter
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.propagators.textmap import Getter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

LOGGER = logging.getLogger(__name__)

TRACE_PHASE = "etos.phase"
TRACE_OUTCOME = "etos.outcome"
TRACE_API_VERSION = "etos.api_version"
METER = metrics.get_meter(__name__)
MILESTONE_COUNTER = METER.create_counter(
    "etos.suite_runner.milestones",
    description="Suite-runner lifecycle milestones.",
)
MILESTONE_DURATION = METER.create_histogram(
    "etos.suite_runner.milestone.duration",
    unit="ms",
    description="Suite-runner lifecycle milestone duration.",
)


def flush() -> None:
    """Best-effort flush of spans and metrics before the short-lived job exits."""
    for provider, kind in (
        (trace.get_tracer_provider(), "spans"),
        (metrics.get_meter_provider(), "metrics"),
    ):
        force_flush = getattr(provider, "force_flush", None)
        if force_flush is None:
            continue
        try:
            force_flush(timeout_millis=5_000)  # pylint:disable=not-callable
        except Exception:  # pylint:disable=broad-exception-caught
            LOGGER.exception("Unable to flush OpenTelemetry %s.", kind)


def api_version_for(params) -> str | None:
    """Return the bounded API provenance available for a suite-runner run."""
    return api_version_for_controller(getattr(params, "etos_controller", False))


def api_version_for_controller(controller: bool) -> str:
    """Return the configured API version or the appropriate compatibility default."""
    api_version = os.getenv("ETOS_API_VERSION")
    if api_version:
        return api_version[:64]
    return "v1alpha" if controller else "v0"


def record_milestone(
    span,
    name: str,
    phase: str,
    outcome: str | None = None,
    duration_ms: float | None = None,
) -> None:
    """Record one bounded suite-runner milestone on a span."""
    attributes = {TRACE_PHASE: phase}
    if outcome is not None:
        attributes[TRACE_OUTCOME] = outcome
    span.add_event(name, attributes=attributes)
    MILESTONE_COUNTER.add(1, attributes)
    if duration_ms is not None:
        MILESTONE_DURATION.record(duration_ms, attributes)


@contextmanager
def milestone_span(  # pylint:disable=too-many-arguments
    tracer,
    name: str,
    phase: str,
    *,
    context=None,
    api_version: str | None = None,
    outcome: str | None = None,
):
    """Create a bounded span for a suite-runner lifecycle milestone."""
    with tracer.start_as_current_span(name, context=context) as span:
        started_at = time.monotonic()
        span.set_attribute(TRACE_PHASE, phase)
        if api_version is not None:
            span.set_attribute(TRACE_API_VERSION, api_version)
        if outcome is not None:
            span.set_attribute(TRACE_OUTCOME, outcome)
        record_milestone(span, f"{phase}.started", phase)
        try:  # pylint:disable=no-else-raise
            yield span
        except Exception as exc:
            span.set_attribute(TRACE_OUTCOME, "error")
            record_milestone(
                span,
                f"{phase}.finished",
                phase,
                "error",
                (time.monotonic() - started_at) * 1000,
            )
            OpenTelemetryBase._record_exception(exc)  # pylint:disable=protected-access
            raise
        else:
            if outcome is not None:
                span.set_attribute(TRACE_OUTCOME, outcome)
            record_milestone(
                span,
                f"{phase}.finished",
                phase,
                outcome,
                (time.monotonic() - started_at) * 1000,
            )


class OpenTelemetryBase:
    """Base functionality for OpenTelemetry data collection."""

    # pylint: disable=too-few-public-methods
    @staticmethod
    def _record_exception(exc) -> None:
        """Record the given exception to the current OpenTelemetry span."""
        span = opentelemetry.trace.get_current_span()
        span.set_attribute("error.type", exc.__class__.__name__)
        span.record_exception(exc)
        span.set_status(opentelemetry.trace.Status(opentelemetry.trace.StatusCode.ERROR))


class EnvVarContextGetter(Getter):
    """OpenTelemetry context getter class for environment variables."""

    def get(self, carrier, key):
        """Get value using the given carrier variable and key."""
        value = os.getenv(carrier)
        if value is not None and value != "":
            pairs = value.split(",")
            for pair in pairs:
                k, v = pair.split("=", 1)
                if k == key:
                    return [v]
        return []

    def keys(self, carrier):
        """Get keys of the given carrier variable."""
        value = os.getenv(carrier)
        if value is not None:
            return [pair.split("=")[0] for pair in value.split(",") if "=" in pair]
        return []


def get_current_context() -> opentelemetry.context.context.Context:
    """Get current context propagated via environment variables."""
    propagator = CompositePropagator(
        (
            TraceContextTextMapPropagator(),
            W3CBaggagePropagator(),
        )
    )
    ctx = opentelemetry.context.get_current()
    old_style_traceparent = EnvironmentGetter().get(os.environ, "OTEL_CONTEXT")
    if old_style_traceparent is not None:
        LOGGER.warning(
            "OTEL_CONTEXT environment variable is deprecated; use TRACEPARENT and BAGGAGE "
            "variables instead."
        )
        ctx = propagator.extract(carrier="OTEL_CONTEXT", context=ctx, getter=EnvVarContextGetter())
    # The EnvironmentGetter reads from the carrier, so the environment must be passed as carrier.
    ctx = propagator.extract(carrier=os.environ, context=ctx, getter=EnvironmentGetter())
    return ctx
