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
"""Regression tests for suite-runner observation and trace ancestry."""

from unittest import TestCase, mock
from types import SimpleNamespace

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from etos_suite_runner.esr import ESR
from etos_suite_runner.lib.suite import SubSuite, TestSuite as SuiteHandler


class TestSuiteTraceSemantics(TestCase):
    """Verify observation names, terminal conclusions, and active parent preservation."""

    def test_sub_suite_names_and_conclusions(self):
        """Controller observation and legacy execution retain bounded conclusions."""
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("review-test")
        try:
            for controller, suffix in ((True, "observe"), (False, "execute")):
                for conclusion in ("SUCCESSFUL", "FAILED", "custom"):
                    with self.subTest(controller=controller, conclusion=conclusion):
                        suite = SubSuite(mock.Mock(), {"name": "sub"}, "main", controller)
                        suite.etos.debug.default_test_result_timeout = 60
                        suite.otel_tracer = tracer
                        suite.test_suite_started = {"meta": {"id": "sub"}}
                        suite.test_suite_finished = {
                            "data": {"testSuiteOutcome": {"conclusion": conclusion}}
                        }
                        suite.release = mock.Mock()
                        with mock.patch("etos_suite_runner.lib.suite.time.sleep"):
                            with tracer.start_as_current_span("parent") as parent:
                                suite._start("run")  # pylint:disable=protected-access
                        span = exporter.get_finished_spans()[-2]
                        self.assertEqual(span.name, f"etos.suite_runner.sub_suite.{suffix}")
                        self.assertEqual(span.parent.span_id, parent.get_span_context().span_id)
                        expected = conclusion.lower() if conclusion != "custom" else "unknown"
                        self.assertEqual(span.attributes["etos.conclusion"], expected)
                        self.assertEqual(span.events[-1].attributes["etos.outcome"], expected)
        finally:
            provider.shutdown()

    def test_start_keeps_active_suite_parent(self):
        """The suite wrapper must not replace an active worker span with its old carrier."""
        provider = TracerProvider()
        handler = SuiteHandler(
            mock.Mock(),
            mock.Mock(),
            SimpleNamespace(name="suite"),
            "main",
            {"traceparent": "00-aaaabbbbccccddddeeeeffff00001111-1234567890abcdef-01"},
        )
        captured = []

        def capture():
            """Capture the current span inside the wrapped suite start."""
            captured.append(trace.get_current_span().get_span_context())

        handler._start = capture  # pylint:disable=protected-access
        try:
            with provider.get_tracer("review-test").start_as_current_span("worker") as parent:
                handler.start()
                self.assertEqual(captured, [parent.get_span_context()])
                self.assertEqual(trace.get_current_span().get_span_context(), captured[0])
        finally:
            del handler
            provider.shutdown()

    def test_esr_run_activates_local_span_under_propagated_api_context(self):
        """ESR startup logs use a suite-runner span rather than the API parent directly."""
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        tracer = provider.get_tracer("review-test")
        runner = ESR.__new__(ESR)
        runner.otel_tracer = tracer
        runner.etos = mock.Mock()
        runner.event_publisher = mock.Mock()
        result = mock.Mock()
        result.model_dump.return_value = {}
        runner.result = mock.Mock(return_value=result)
        active_spans = []
        setattr(
            runner,
            "_run",
            mock.Mock(side_effect=lambda: active_spans.append(trace.get_current_span())),
        )

        try:
            with tracer.start_as_current_span("api") as api_span:
                runner.otel_context = trace.set_span_in_context(api_span)
                self.assertIs(runner.run(), result)
                self.assertEqual(trace.get_current_span().name, "api")

            run_span = next(
                span
                for span in exporter.get_finished_spans()
                if span.name == "etos.suite_runner.run"
            )
            self.assertEqual(active_spans[0].name, "etos.suite_runner.run")
            self.assertEqual(run_span.parent.span_id, api_span.get_span_context().span_id)
            runner.event_publisher.publish_shutdown.assert_called_once_with({})
        finally:
            provider.shutdown()

    def test_sub_suite_cleanup_activates_span_for_release_and_logs(self):
        """The cleanup span scopes environment release and its check-in messages."""
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        suite = SubSuite(
            mock.Mock(),
            {"id": "environment-id", "executor": {"id": "executor-id"}},
            "main-suite-id",
            True,
        )
        suite.otel_tracer = provider.get_tracer("review-test")
        active_span_ids = []

        def record_current_span(*_args, **_kwargs):
            """Capture the span for cleanup operations and log messages."""
            active_span_ids.append(trace.get_current_span().get_span_context().span_id)
            return True

        setattr(suite, "_delete_environment", mock.Mock(side_effect=record_current_span))
        try:
            with mock.patch.object(suite.logger, "info", side_effect=record_current_span):
                suite.release("testrun-id")

            cleanup_span = next(
                span
                for span in exporter.get_finished_spans()
                if span.name == "etos.suite_runner.sub_suite.cleanup"
            )
            self.assertEqual(active_span_ids, [cleanup_span.context.span_id] * 3)
            self.assertEqual(cleanup_span.attributes["etos.outcome"], "completed")
        finally:
            provider.shutdown()
