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
import logging
import threading
import time
from typing import Iterator

from eiffellib.events import EiffelTestSuiteStartedEvent
from environment_provider.lib.registry import ProviderRegistry
from environment_provider.environment import release_environment
from etos_lib import ETOS
from etos_lib.logging.logger import FORMAT_CONFIG
from jsontas.jsontas import JsonTas

from .esr_parameters import ESRParameters
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


class SubSuite:  # pylint:disable=too-many-instance-attributes
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

    def start(self, identifier: str) -> None:
        """Start ETR for this sub suite.

        :param identifier: An identifier for logs in this sub suite.
        """
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

    def release(self, testrun_id) -> None:
        """Release this sub suite."""
        # TODO: This whole method is now a bit of a hack that needs to be cleaned up.
        # Most cleanup is required in the environment provider so this method will stay until an
        # update has been made there.
        self.logger.info(
            "Check in test environment %r", self.environment["id"], extra={"user_log": True}
        )
        jsontas = JsonTas()
        registry = ProviderRegistry(etos=self.etos, jsontas=jsontas, suite_id=testrun_id)

        self.logger.info(self.environment)
        success = release_environment(
            etos=self.etos, jsontas=jsontas, provider_registry=registry, sub_suite=self.environment
        )
        if not success:
            self.logger.exception(
                "Failed to check in %r", self.environment["id"], extra={"user_log": True}
            )
            return
        self.logger.info("Checked in %r", self.environment["id"], extra={"user_log": True})
        self.released = True


class TestSuite:  # pylint:disable=too-many-instance-attributes
    """Handle the starting and waiting for test suites in ETOS."""

    test_suite_started = None
    started = False
    empty = False
    __activity_triggered = None
    __activity_finished = None

    def __init__(self, etos: ETOS, params: ESRParameters, suite: dict) -> None:
        """Initialize a TestSuite instance."""
        self.etos = etos
        self.params = params
        self.suite = suite
        self.logger = logging.getLogger(f"TestSuite - {self.suite.get('name')}")
        self.logger.addFilter(DuplicateFilter(self.logger))
        self.sub_suites = []

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
                self.suite["test_suite_started_id"]
            )
            if activity_triggered is None:
                status = self.params.get_status()
                if status.get("status") == "FAILURE":
                    raise EnvironmentProviderException(
                        status.get("error"), self.etos.config.get("task_id")
                    )
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
                    raise EnvironmentProviderException(
                        activity_finished["data"]["activityOutcome"]["description"],
                        self.etos.config.get("task_id"),
                    )
                if len(environments) > 0:  # Must be at least 1 sub suite.
                    return
        else:  # pylint:disable=useless-else-on-loop
            raise TimeoutError(
                f"Timed out after {self.etos.config.get('WAIT_FOR_ENVIRONMENT_TIMEOUT')} seconds."
            )

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

    def _announce(self, header: str, body: str) -> None:
        """Send an announcement over Eiffel.

        :param header: Header of the announcement.
        :param body: Body of the announcement.
        """
        self.etos.events.send_announcement_published(
            f"[ESR] {header}",
            body,
            "MINOR",
            {"CONTEXT": self.etos.config.get("context")},
        )

    def _send_test_suite_started(self) -> EiffelTestSuiteStartedEvent:
        """Send a test suite started event.

        :return: Test suite started event.
        """
        test_suite_started = EiffelTestSuiteStartedEvent()

        categories = ["Regression test suite"]
        if self.params.product:
            categories.append(self.params.product)

        # This ID has been stored in Environment so that the ETR know which test suite to link to.
        test_suite_started.meta.event_id = self.suite.get("test_suite_started_id")
        data = {
            "name": self.suite.get("name"),
            "categories": categories,
            "types": ["FUNCTIONAL"],
        }
        links = {
            "CONTEXT": self.etos.config.get("context"),
            "TERC": self.params.tercc.meta.event_id,
        }
        return self.etos.events.send(test_suite_started, links, data)

    def start(self) -> None:
        """Send test suite started, trigger and wait for all sub suites to start."""
        self._announce("Starting tests", f"Starting up sub suites for '{self.suite.get('name')}'")

        self.test_suite_started = self._send_test_suite_started()
        self.logger.info("Test suite started %r", self.test_suite_started.meta.event_id)
        if len(self.suite.get("recipes")) == 0:
            self.logger.error("Not recipes found in test suite. Exiting.")
            self.empty = True
            return

        self.logger.info("Starting sub suites")
        threads = []
        try:
            self.logger.info(
                "Waiting for an environment for %r (%r)",
                self.suite.get("name"),
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
                    self.etos, sub_suite_definition, self.suite["test_suite_started_id"]
                )
                self.sub_suites.append(sub_suite)
                thread = threading.Thread(
                    target=sub_suite.start, args=(self.params.tercc.meta.event_id,)
                )
                threads.append(thread)
                thread.start()
            self.logger.info(
                "All sub suites for %r (%r) have now been triggered",
                self.suite.get("name"),
                self.test_suite_started.meta.event_id,
                extra={"user_log": True},
            )
            self.logger.info(
                "Total count of sub suites for %r (%r): %d",
                self.suite.get("name"),
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
            self.suite.get("name"),
            self.test_suite_started.meta.event_id,
            extra={"user_log": True},
        )

    def release_all(self) -> None:
        """Release all, unreleased, sub suites."""
        self.logger.info("Releasing all sub suite environments")
        for sub_suite in self.sub_suites:
            if not sub_suite.released:
                sub_suite.release(self.params.tercc.meta.event_id)
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
            description = (
                f"No tests in test suite {self.params.tercc.meta.event_id}, aborting test run"
            )
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
            self.suite.get("name"),
            self.test_suite_started.meta.event_id,
            verdict,
            conclusion,
            description,
            extra={"user_log": True},
        )
        return verdict, conclusion, description
