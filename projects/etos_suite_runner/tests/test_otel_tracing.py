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
"""Tests for suite-runner context propagation and bounded tracing helpers."""

import os
import threading
from unittest import TestCase, mock
from unittest.mock import patch

from opentelemetry import baggage, trace

from etos_suite_runner.lib.otel_tracing import get_current_context
from etos_suite_runner.lib import otel_tracing
from etos_suite_runner.lib.otel_tracing import (
    TRACE_API_VERSION,
    TRACE_OUTCOME,
    TRACE_PHASE,
    api_version_for,
    api_version_for_controller,
    milestone_span,
)
from etos_suite_runner.lib.runner import SuiteRunner

TRACE_ID = "aaaabbbbccccddddeeeeffff00001111"
SPAN_ID = "1234567890abcdef"
TRACEPARENT = f"00-{TRACE_ID}-{SPAN_ID}-01"


class TestOpenTelemetryContext(TestCase):
    """Test the extraction of the OpenTelemetry context from environment variables."""

    def test_context_from_traceparent(self):
        """Test that the parent span is extracted from the TRACEPARENT environment variable.

        Approval criteria:
            - The span context shall have the trace and span IDs of the TRACEPARENT.
            - The baggage shall be extracted from the BAGGAGE environment variable.

        Test steps:
            1. Get the current context with TRACEPARENT and BAGGAGE set.
            2. Verify that the span context and baggage are extracted.
        """
        environment = {"TRACEPARENT": TRACEPARENT, "BAGGAGE": "testrun_id=abc"}
        with mock.patch.dict(os.environ, environment):
            ctx = get_current_context()
        span_context = trace.get_current_span(ctx).get_span_context()
        self.assertTrue(span_context.is_valid)
        self.assertTrue(span_context.is_remote)
        self.assertEqual(span_context.trace_id, int(TRACE_ID, 16))
        self.assertEqual(span_context.span_id, int(SPAN_ID, 16))
        self.assertEqual(baggage.get_baggage("testrun_id", ctx), "abc")

    def test_context_from_deprecated_otel_context(self):
        """Test that the parent span is extracted from the deprecated OTEL_CONTEXT variable.

        Approval criteria:
            - The span context shall have the trace and span IDs of the OTEL_CONTEXT traceparent.

        Test steps:
            1. Get the current context with OTEL_CONTEXT set.
            2. Verify that the span context is extracted.
        """
        environment = {"OTEL_CONTEXT": f"traceparent={TRACEPARENT}"}
        with mock.patch.dict(os.environ, environment):
            os.environ.pop("TRACEPARENT", None)
            ctx = get_current_context()
        span_context = trace.get_current_span(ctx).get_span_context()
        self.assertTrue(span_context.is_valid)
        self.assertEqual(span_context.trace_id, int(TRACE_ID, 16))

    def test_no_context(self):
        """Test that no parent span is extracted when no trace context is set.

        Approval criteria:
            - The span context shall be invalid.

        Test steps:
            1. Get the current context without TRACEPARENT or OTEL_CONTEXT set.
            2. Verify that the span context is invalid.
        """
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("TRACEPARENT", None)
            os.environ.pop("OTEL_CONTEXT", None)
            ctx = get_current_context()
        self.assertFalse(trace.get_current_span(ctx).get_span_context().is_valid)


class TestSuiteRunnerContext(TestCase):
    """Test the OpenTelemetry context in the suite runner thread pool."""

    def test_run_attaches_suite_context(self):
        """Test that a test suite is started and finished within its OpenTelemetry context.

        Approval criteria:
            - The test suite shall be started, finished and released within the context of the
              carrier given to the test suite, also when running in a separate thread.

        Test steps:
            1. Run a test suite with a trace context carrier in a separate thread.
            2. Verify that start, finish and release were called within the trace context.
        """
        span_contexts = {}

        def record(name):
            """Record the current span context for a test suite method."""

            def _record(*_):
                """Store the current span context."""
                span_contexts[name] = trace.get_current_span().get_span_context()
                return ("PASSED", "SUCCESSFUL", "")

            return _record

        test_suite = mock.MagicMock()
        test_suite.otel_context_carrier = {"traceparent": TRACEPARENT}
        test_suite.start.side_effect = record("start")
        test_suite.results.side_effect = record("results")
        test_suite.finish.side_effect = record("finish")
        test_suite.release_all.side_effect = record("release_all")

        runner = SuiteRunner(mock.MagicMock(), mock.MagicMock())
        thread = threading.Thread(target=runner.run, args=(test_suite,))
        thread.start()
        thread.join()

        self.assertEqual(set(span_contexts), {"start", "results", "finish", "release_all"})
        for name, span_context in span_contexts.items():
            self.assertEqual(span_context.trace_id, int(TRACE_ID, 16), name)
            self.assertEqual(span_context.span_id, int(SPAN_ID, 16), name)


class FakeSpan:  # pylint:disable=too-few-public-methods
    """Minimal span test double."""

    def __init__(self):
        """Initialize recorded attributes and events."""
        self.attributes = {}
        self.events = []

    def set_attribute(self, key, value):
        """Record a span attribute."""
        self.attributes[key] = value

    def add_event(self, name, attributes=None):
        """Record a span event."""
        self.events.append((name, attributes))


class FakeTracer:  # pylint:disable=too-few-public-methods
    """Minimal tracer test double."""

    def __init__(self):
        """Create a recording span."""
        self.span = FakeSpan()
        self.name = None

    def start_as_current_span(self, name, context=None):  # pylint:disable=unused-argument
        """Return the recording span context manager."""
        self.name = name
        return _SpanContext(self.span)


class _SpanContext:
    """Provide a context manager for a recording span."""

    def __init__(self, span):
        """Store the managed span."""
        self.span = span

    def __enter__(self):
        """Return the managed span."""
        return self.span

    def __exit__(self, exc_type, exc_value, traceback):
        """Propagate exceptions to the caller."""
        return False


class TestOtelTracing(TestCase):
    """Test bounded attributes and milestone events."""

    def test_milestone_span_records_bounded_start_and_finish(self):
        """Verify milestone events and bounded metric dimensions."""
        tracer = FakeTracer()

        with patch.object(otel_tracing.MILESTONE_COUNTER, "add") as counter_add:
            with patch.object(otel_tracing.MILESTONE_DURATION, "record") as duration_record:
                with milestone_span(
                    tracer,
                    "etos.suite_runner",
                    "suite_runner",
                    api_version="v1alpha",
                    outcome="completed",
                ):
                    pass

        self.assertEqual(tracer.span.attributes[TRACE_PHASE], "suite_runner")
        self.assertEqual(tracer.span.attributes[TRACE_API_VERSION], "v1alpha")
        self.assertEqual(tracer.span.attributes[TRACE_OUTCOME], "completed")
        self.assertEqual(
            tracer.span.events,
            [
                ("suite_runner.started", {TRACE_PHASE: "suite_runner"}),
                (
                    "suite_runner.finished",
                    {TRACE_PHASE: "suite_runner", TRACE_OUTCOME: "completed"},
                ),
            ],
        )
        self.assertEqual(counter_add.call_count, 2)
        self.assertEqual(duration_record.call_count, 1)
        metric_attributes = counter_add.call_args.args[1]
        self.assertEqual(set(metric_attributes), {TRACE_PHASE, TRACE_OUTCOME})
        self.assertNotIn("testrun_id", metric_attributes)

    def test_milestone_span_records_error_without_leaking_exception_text(self):
        """Verify error outcomes without recording exception text in milestones."""
        tracer = FakeTracer()

        with patch("etos_suite_runner.lib.otel_tracing.OpenTelemetryBase._record_exception"):
            with self.assertRaisesRegex(ValueError, "secret"):
                with milestone_span(tracer, "etos.suite_runner", "suite_runner"):
                    raise ValueError("secret")

        self.assertEqual(tracer.span.attributes[TRACE_OUTCOME], "error")
        self.assertEqual(
            tracer.span.events[-1],
            ("suite_runner.finished", {TRACE_PHASE: "suite_runner", TRACE_OUTCOME: "error"}),
        )

    def test_api_version_uses_explicit_provenance_before_controller_default(self):
        """Prefer explicit API provenance over the compatibility default."""
        params = type("Params", (), {"etos_controller": True})()

        with patch.dict("os.environ", {"ETOS_API_VERSION": "v1beta1"}):
            self.assertEqual(api_version_for(params), "v1beta1")

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(api_version_for(params), "v1alpha")
            self.assertEqual(
                api_version_for(type("Params", (), {"etos_controller": False})()), "v0"
            )
            self.assertEqual(api_version_for_controller(True), "v1alpha")
            self.assertEqual(api_version_for_controller(False), "v0")

    def test_flush_flushes_providers_and_tolerates_failures(self):
        """Flush both providers and never raise when one of them fails."""
        tracer_provider = mock.Mock()
        meter_provider = mock.Mock()
        tracer_provider.force_flush.side_effect = RuntimeError("boom")

        with patch.object(otel_tracing.trace, "get_tracer_provider", return_value=tracer_provider):
            with patch.object(
                otel_tracing.metrics, "get_meter_provider", return_value=meter_provider
            ):
                otel_tracing.flush()

        tracer_provider.force_flush.assert_called_once()
        meter_provider.force_flush.assert_called_once()
