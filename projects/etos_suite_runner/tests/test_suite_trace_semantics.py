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
