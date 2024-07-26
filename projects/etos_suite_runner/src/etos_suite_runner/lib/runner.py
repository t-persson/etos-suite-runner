# Copyright 2020-2022 Axis Communications AB.
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
"""ETOS suite runner executor."""
import logging
from multiprocessing.pool import ThreadPool

from environment_provider.environment import release_full_environment
from etos_lib.logging.logger import FORMAT_CONFIG
from etos_lib.kubernetes.schemas.testrun import Suite
from jsontas.jsontas import JsonTas
import opentelemetry
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .exceptions import EnvironmentProviderException
from .otel_tracing import get_current_context, OpenTelemetryBase
from .suite import TestSuite


class SuiteRunner(OpenTelemetryBase):  # pylint:disable=too-few-public-methods
    """Test suite runner.

    Splits test suites into sub suites based on number of products available.
    Starts ETOS test runner (ETR) and sends out a test suite finished.
    """

    logger = logging.getLogger("ESR - Runner")

    def __init__(self, params, etos):
        """Initialize.

        :param params: Parameters object for this suite runner.
        :type params: :obj:`etos_suite_runner.lib.esr_parameters.ESRParameters`
        :param etos: ETOS library object.
        :type etos: :obj:`etos_lib.etos.ETOS`
        """
        self.params = params
        self.etos = etos
        self.otel_tracer = opentelemetry.trace.get_tracer(__name__)
        self.otel_suite_context = get_current_context()

    def _release_environment(self):
        """Release an environment from the environment provider."""
        # TODO: We should remove jsontas as a requirement for this function.
        # Passing variables as keyword argument to make it easier to transition to a function where
        # jsontas is not required.
        jsontas = JsonTas()
        span_name = "release_full_environment"
        with self.otel_tracer.start_as_current_span(
            span_name,
            context=self.otel_suite_context,
            kind=opentelemetry.trace.SpanKind.CLIENT,
        ):
            status, message = release_full_environment(
                etos=self.etos, jsontas=jsontas, suite_id=self.params.testrun_id
            )
            if not status:
                self.logger.error(message)

    def start_suites_and_wait(self, suites: list[tuple[str, Suite]]):
        """Get environments and start all test suites."""
        try:
            otel_context_carrier = {}
            TraceContextTextMapPropagator().inject(otel_context_carrier)
            # test_suites = [
            #     TestSuite(self.etos, self.params, suite, otel_context_carrier=otel_context_carrier)
            #     for suite in self.params.test_suite
            # ]
            test_suites = [
                TestSuite(self.etos, self.params, suite, id, otel_context_carrier=otel_context_carrier)
                for id, suite in suites
            ]
            with ThreadPool() as pool:
                pool.map(self.run, test_suites)
            status = self.params.get_status()
            if status.get("error") is not None:
                exc = EnvironmentProviderException(status["error"], self.etos.config.get("task_id"))
                self._record_exception(exc)
                raise exc
        finally:
            self.logger.info("Release the full test environment.")
            self._release_environment()

    def run(self, test_suite):
        """Run test suite runner.

        :param test_suite: Test suite to run.
        :type test_suite: :obj:`TestSuite`
        """
        FORMAT_CONFIG.identifier = self.params.testrun_id
        try:
            test_suite.start()  # send EiffelTestSuiteStartedEvent
            # All sub suites finished.
        finally:
            results = test_suite.results()
            test_suite.finish(*results)  # send EiffelTestSuiteFinishedEvent
            test_suite.release_all()
