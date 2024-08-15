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
"""Test suite handler."""
import os
import json
import logging
import threading
import time
from typing import Iterator, Union

from eiffellib.events import EiffelTestSuiteStartedEvent
from environment_provider.lib.registry import ProviderRegistry
from environment_provider.environment import release_environment
from etos_lib import ETOS
from etos_lib.logging.logger import FORMAT_CONFIG
from etos_lib.opentelemetry.semconv import Attributes as SemConvAttributes
from etos_lib.kubernetes import Kubernetes, Environment
from jsontas.jsontas import JsonTas
import opentelemetry
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .esr_parameters import ESRParameters
from etos_lib.kubernetes.schemas.testrun import Suite
from .exceptions import EnvironmentProviderException
from .executor import Executor, TestStartException
from .graphql import (
    request_activity_finished,
    request_activity_triggered,
    request_environment_defined,
    request_test_suite_finished,
    request_test_suite_started,
)
from .log_filter import DuplicateFilter
from .otel_tracing import OpenTelemetryBase


class SubSuite(OpenTelemetryBase):  # pylint:disable=too-many-instance-attributes
    """Handle test results and tracking of a single sub suite."""

    released = False
    failed = False

    def __init__(self, etos: ETOS, environment: dict, main_suite_id: str) -> None:
        """Initialize a sub suite."""
        self.etos = etos
        self.environment = environment
        self.main_suite_id = main_suite_id
        self.logger = logging.getLogger(f"SubSuite - {self.environment.get('name')}")
        self.logger.addFilter(DuplicateFilter(self.logger))
        self.otel_tracer = opentelemetry.trace.get_tracer(__name__)
        self.test_suite_started = {}
        self.test_suite_finished = {}

    @property
    def finished(self) -> bool:
        """Whether or not this sub suite has finished."""
        return bool(self.test_suite_finished)

    @property
    def started(self) -> bool:
        """Whether or not this sub suite has started."""
        if not bool(self.test_suite_started):
            for test_suite_started in request_test_suite_started(self.etos, self.main_suite_id):
                # Using name to match here is safe because we're only searching for
                # sub suites that are connected to this test_suite_started ID and the
                # "_SubSuite_\d" part of the name is set by ETOS and not humans.
                if self.environment.get("name") == test_suite_started["data"]["name"]:
                    self.test_suite_started = test_suite_started
                    break
        return bool(self.test_suite_started)

    def request_finished_event(self) -> None:
        """Request a test suite finished event for this sub suite."""
        # Prevent ER requests if we know we're not even started.
        if not self.started:
            return
        # Prevent ER requests if we know we're already finished.
        if not self.test_suite_finished:
            self.test_suite_finished = request_test_suite_finished(
                self.etos, self.test_suite_started["meta"]["id"]
            )

    def outcome(self) -> dict:
        """Outcome of this sub suite.

        :return: Test suite outcome from the test suite finished event.
        """
        if self.finished:
            return self.test_suite_finished.get("data", {}).get("testSuiteOutcome", {})
        return {}

    def _start(self, identifier: str) -> None:
        """Start ETR for this sub suite.

        :param identifier: An identifier for logs in this sub suite.
        """
        span_name = "execute_testrunner"
        with self.otel_tracer.start_as_current_span(
            span_name,
            kind=opentelemetry.trace.SpanKind.CLIENT,
        ) as span:
            span.set_attribute(SemConvAttributes.SUBSUITE_ID, identifier)
            FORMAT_CONFIG.identifier = identifier
            self.logger.info("Starting up the ETOS test runner", extra={"user_log": True})
            executor = Executor(self.etos)
            try:
                executor.run_tests(self.environment)
            except TestStartException as exception:
                self.failed = True
                self.logger.error(
                    "Failed to start sub suite: %s", exception.error, extra={"user_log": True}
                )
                self._record_exception(exception)
                raise
            self.logger.info("ETR triggered.")
            timeout = time.time() + self.etos.debug.default_test_result_timeout
            try:
                while time.time() < timeout:
                    time.sleep(10)
                    if not self.started:
                        continue
                    self.logger.info("ETOS test runner has started", extra={"user_log": True})
                    self.request_finished_event()
                    if self.finished:
                        self.logger.info("ETOS test runner has finished", extra={"user_log": True})
                        break
            finally:
                self.release(identifier)

    def start(self, identifier: str, otel_context_carrier: dict) -> None:
        """Start ETR for this sub suite (OpenTelemetry wrapper method).

        :param identifier: An identifier for logs in this sub suite.
        :otel_context_carrier: a dict propagating OpenTelemetry context from the parent thread.
        """
        # OpenTelemetry context needs to be explicitly given here when creating this new span.
        # This is because the subsuite is running in a separate thread.
        otel_context = TraceContextTextMapPropagator().extract(carrier=otel_context_carrier)
        otel_context_token = opentelemetry.context.attach(otel_context)
        try:
            self._start(identifier)
        finally:
            opentelemetry.context.detach(otel_context_token)

    def _delete_environment(self) -> bool:
        """Delete environment from Kubernetes."""
        environment_name = self.environment.get("executor", {}).get("id")
        environment_client = Environment(Kubernetes(), strict=True)
        return environment_client.delete(environment_name)

    def _release_environment(self, testrun_id: str):
        """Release environment manually via the environment provider."""
        # TODO: This whole method is now a bit of a hack that needs to be cleaned up.
        # Most cleanup is required in the environment provider so this method will stay until an
        # update has been made there.
        jsontas = JsonTas()
        registry = ProviderRegistry(etos=self.etos, jsontas=jsontas, suite_id=testrun_id)

        self.logger.info(self.environment)

        span_name = "release_environment"
        with self.otel_tracer.start_as_current_span(
            span_name,
            kind=opentelemetry.trace.SpanKind.CLIENT,
        ) as span:
            failure = release_environment(
                etos=self.etos,
                jsontas=jsontas,
                provider_registry=registry,
                sub_suite=self.environment,
            )
            span.set_attribute(SemConvAttributes.TESTRUN_ID, testrun_id)
            span.set_attribute(SemConvAttributes.ENVIRONMENT, json.dumps(self.environment))
            if failure is not None:
                self._record_exception(failure)
                return False
        return True

    def release(self, testrun_id) -> None:
        """Release this sub suite."""
        self.logger.info(
            "Check in test environment %r", self.environment["id"], extra={"user_log": True}
        )
        # Running as part of ETOS controller
        if os.getenv("IDENTIFIER") is not None:
            success = self._delete_environment()
        else:
            success = self._release_environment(testrun_id)

        if not success:
            self.logger.exception(
                "Failed to check in %r", self.environment["id"], extra={"user_log": True}
            )
            return
        self.logger.info("Checked in %r", self.environment["id"], extra={"user_log": True})
        self.released = True


class TestSuite(OpenTelemetryBase):  # pylint:disable=too-many-instance-attributes
    """Handle the starting and waiting for test suites in ETOS."""

    test_suite_started = None
    started = False
    empty = False
    __activity_triggered = None
    __activity_finished = None

    def __init__(
        self,
        etos: ETOS,
        params: ESRParameters,
        suite: Suite,
        id: str,
        otel_context_carrier: Union[dict, None] = None,
    ) -> None:
        """Initialize a TestSuite instance."""
        self.etos = etos
        self.params = params
        self.suite = suite
        self.test_suite_started_id = id
        self.logger = logging.getLogger(f"TestSuite - {self.suite.name}")
        self.logger.addFilter(DuplicateFilter(self.logger))
        self.sub_suites = []

        if otel_context_carrier is None:
            otel_context_carrier = {}

        self.otel_context_carrier = otel_context_carrier
        self.otel_context = TraceContextTextMapPropagator().extract(
            carrier=self.otel_context_carrier
        )
        self.otel_context_token = opentelemetry.context.attach(self.otel_context)
        TraceContextTextMapPropagator().inject(self.otel_context_carrier)

    def __del__(self):
        """Destructor."""
        opentelemetry.context.detach(self.otel_context_token)

    @property
    def sub_suite_environments(self) -> Iterator[dict]:
        """All sub suite environments from the environment provider.

        Each sub suite environment is an environment for the sub suites to execute in.
        """
        self.logger.debug(
            "Start collecting sub suite definitions (timeout=%ds).",
            self.etos.config.get("WAIT_FOR_ENVIRONMENT_TIMEOUT"),
        )
        environments = []
        timeout = time.time() + self.etos.config.get("WAIT_FOR_ENVIRONMENT_TIMEOUT")
        while time.time() < timeout:
            time.sleep(5)
            activity_triggered = self.__environment_activity_triggered(
                self.test_suite_started_id
            )
            if activity_triggered is None:
                status = self.params.get_status()
                if status.get("status") == "FAILURE":
                    exc = EnvironmentProviderException(
                        status.get("error"), self.etos.config.get("task_id")
                    )
                    self._record_exception(exc)
                    raise exc
                continue
            activity_finished = self.__environment_activity_finished(
                activity_triggered["meta"]["id"]
            )
            for environment in request_environment_defined(
                self.etos, activity_triggered["meta"]["id"]
            ):
                if environment["meta"]["id"] not in environments:
                    environments.append(environment["meta"]["id"])
                    yield environment
            if activity_finished is not None:
                if activity_finished["data"]["activityOutcome"]["conclusion"] != "SUCCESSFUL":
                    exc = EnvironmentProviderException(
                        activity_finished["data"]["activityOutcome"]["description"],
                        self.etos.config.get("task_id"),
                    )
                    self._record_exception(exc)
                    raise exc
                if len(environments) > 0:  # Must be at least 1 sub suite.
                    return
        else:  # pylint:disable=useless-else-on-loop
            exc = TimeoutError(
                f"Timed out after {self.etos.config.get('WAIT_FOR_ENVIRONMENT_TIMEOUT')} seconds."
            )
            self._record_exception(exc)
            raise exc

    @property
    def all_finished(self) -> bool:
        """Whether or not all sub suites are finished."""
        return all(sub_suite.finished for sub_suite in self.sub_suites)

    def __environment_activity_triggered(self, test_suite_started_id: str) -> dict:
        """Activity triggered event from the environment provider.

        :param test_suite_started_id: The ID that the activity triggered links to.
        :return: An activity triggered event dictionary.
        """
        if self.__activity_triggered is None:
            self.__activity_triggered = request_activity_triggered(self.etos, test_suite_started_id)
        return self.__activity_triggered

    def __environment_activity_finished(self, activity_triggered_id: str) -> dict:
        """Activity finished event from the environment provider.

        :param activity_triggered_id: The ID that the activity finished links to.
        :return: An activity finished event dictionary.
        """
        if self.__activity_finished is None:
            self.__activity_finished = request_activity_finished(self.etos, activity_triggered_id)
        return self.__activity_finished

    def _download_sub_suite(self, environment: dict) -> dict:
        """Download a sub suite from an EnvironmentDefined event.

        :param environment: Environment defined event to download from.
        :return: Downloaded sub suite information.
        """
        if environment["data"].get("uri") is None:
            return None
        uri = environment["data"]["uri"]
        json_header = {"Accept": "application/json"}

        response = self.etos.http.get(uri, headers=json_header)
        response.raise_for_status()
        return response.json()

    def _send_test_suite_started(self) -> EiffelTestSuiteStartedEvent:
        """Send a test suite started event.

        :return: Test suite started event.
        """
        test_suite_started = EiffelTestSuiteStartedEvent()

        categories = ["Regression test suite"]
        if self.params.product:
            categories.append(self.params.product)

        # This ID has been stored in Environment so that the ETR know which test suite to link to.
        test_suite_started.meta.event_id = self.test_suite_started_id
        data = {
            "name": self.suite.name,
            "categories": categories,
            "types": ["FUNCTIONAL"],
        }
        links = {
            "CONTEXT": self.etos.config.get("context"),
            "TERC": self.params.testrun_id,
        }
        return self.etos.events.send(test_suite_started, links, data)

    def _start(self):
        """Send test suite started, trigger and wait for all sub suites to start."""
        self.test_suite_started = self._send_test_suite_started()
        self.logger.info("Test suite started %r", self.test_suite_started.meta.event_id)
        if len(self.suite.tests) == 0:
            self.logger.error("Not recipes found in test suite. Exiting.")
            self.empty = True
            return

        self.logger.info("Starting sub suites")
        threads = []
        try:
            self.logger.info(
                "Waiting for an environment for %r (%r)",
                self.suite.name,
                self.test_suite_started.meta.event_id,
                extra={"user_log": True},
            )
            for sub_suite_environment in self.sub_suite_environments:
                self.logger.info(
                    "Environment received. Starting up a sub suite", extra={"user_log": True}
                )
                sub_suite_definition = self._download_sub_suite(sub_suite_environment)
                if sub_suite_definition is None:
                    raise EnvironmentProviderException(
                        "URL to sub suite is missing", self.etos.config.get("task_id")
                    )
                sub_suite_definition["id"] = sub_suite_environment["meta"]["id"]
                sub_suite = SubSuite(
                    self.etos, sub_suite_definition, self.test_suite_started_id
                )
                self.sub_suites.append(sub_suite)
                thread = threading.Thread(
                    target=sub_suite.start,
                    args=(self.params.testrun_id, self.otel_context_carrier),
                )
                threads.append(thread)
                thread.start()
            self.logger.info(
                "All sub suites for %r (%r) have now been triggered",
                self.suite.name,
                self.test_suite_started.meta.event_id,
                extra={"user_log": True},
            )
            self.logger.info(
                "Total count of sub suites for %r (%r): %d",
                self.suite.name,
                self.test_suite_started.meta.event_id,
                len(self.sub_suites),
                extra={"user_log": True},
            )
            self.started = True
        finally:
            for thread in threads:
                thread.join()

        self.logger.info(
            "All sub suites for %r (%r) have now finished",
            self.suite.name,
            self.test_suite_started.meta.event_id,
            extra={"user_log": True},
        )

    def start(self) -> None:
        """Send test suite started, trigger and wait for all sub suites to start.

        This is an OpenTelemetry wrapper method for _start().
        """
        # OpenTelemetry contexts aren't automatically propagated to threads.
        # For this reason OpenTelemetry context needs to be reinstantiated here.
        otel_context = TraceContextTextMapPropagator().extract(carrier=self.otel_context_carrier)
        otel_context_token = opentelemetry.context.attach(otel_context)
        try:
            self._start()
        finally:
            opentelemetry.context.detach(otel_context_token)

    def release_all(self) -> None:
        """Release all, unreleased, sub suites."""
        self.logger.info("Releasing all sub suite environments")
        for sub_suite in self.sub_suites:
            if not sub_suite.released:
                sub_suite.release(self.params.testrun_id)
        self.logger.info("All sub suite environments are released")

    def finish(self, verdict: str, conclusion: str, description: str) -> None:
        """Send test suite finished for this test suite.

        :param verdict: Verdict of the execution.
        :param conclusion: Conclusion taken on the results.
        :param description: Description of the verdict and conclusion.
        """
        self.etos.events.send_test_suite_finished(
            self.test_suite_started,
            {"CONTEXT": self.etos.config.get("context")},
            outcome={
                "verdict": verdict,
                "conclusion": conclusion,
                "description": description,
            },
        )
        self.logger.info("Test suite finished.")

    def results(self) -> tuple[str, str, str]:
        """Test results for this execution.

        :return: Verdict, conclusion and description.
        """
        verdict = "INCONCLUSIVE"
        conclusion = "SUCCESSFUL"
        description = ""
        failed = [sub_suite for sub_suite in self.sub_suites if sub_suite.failed]

        if self.empty:
            verdict = "INCONCLUSIVE"
            conclusion = "FAILED"
            description = f"No tests in test suite {self.params.testrun_id}, aborting test run"
        elif not self.started:
            verdict = "INCONCLUSIVE"
            conclusion = "FAILED"
            description = (
                f"No sub suites started at all for {self.test_suite_started.meta.event_id}."
            )
        elif failed:
            verdict = "INCONCLUSIVE"
            conclusion = "FAILED"
            description = f"{len(failed)} sub suites failed to start"
        elif not self.all_finished:
            verdict = "INCONCLUSIVE"
            conclusion = "FAILED"
            description = "Did not receive test results from sub suites."
        else:
            for sub_suite in self.sub_suites:
                if sub_suite.outcome().get("verdict") != "PASSED":
                    verdict = "FAILED"
                description = sub_suite.outcome().get("description")
            # If we get this far without exceptions or return statements
            # and the verdict is still inconclusive, it would mean that
            # that we passed everything.
            if verdict == "INCONCLUSIVE":
                description = "All tests passed."
                verdict = "PASSED"
            if not description:
                description = "No description received from ESR or ETR."
        self.logger.info(
            "Test suite result for %r (%r): %r,%r,%r",
            self.suite.name,
            self.test_suite_started.meta.event_id,
            verdict,
            conclusion,
            description,
            extra={"user_log": True},
        )
        return verdict, conclusion, description
