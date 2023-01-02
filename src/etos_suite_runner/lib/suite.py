# Copyright 2022 Axis Communications AB.
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

from etos_lib.logging.logger import FORMAT_CONFIG
from eiffellib.events import EiffelTestSuiteStartedEvent

from .executor import Executor
from .graphql import (
    request_test_suite_finished,
    request_test_suite_started,
    request_environment_defined,
    request_activity_triggered,
    request_activity_finished,
)
from .log_filter import DuplicateFilter
from .exceptions import EnvironmentProviderException


class SubSuite:
    """Handle test results and tracking of a single sub suite."""

    released = False

    def __init__(self, etos, environment, main_suite_id):
        """Initialize a sub suite."""
        self.etos = etos
        self.environment = environment
        self.main_suite_id = main_suite_id
        self.logger = logging.getLogger(f"SubSuite - {self.environment.get('name')}")
        self.logger.addFilter(DuplicateFilter(self.logger))
        self.test_suite_started = {}
        self.test_suite_finished = {}

    @property
    def finished(self):
        """Whether or not this sub suite has finished."""
        return bool(self.test_suite_finished)

    @property
    def started(self):
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

    def request_finished_event(self):
        """Request a test suite finished event for this sub suite."""
        # Prevent ER requests if we know we're not even started.
        if not self.started:
            return
        # Prevent ER requests if we know we're already finished.
        if not self.test_suite_finished:
            self.test_suite_finished = request_test_suite_finished(
                self.etos, self.test_suite_started["meta"]["id"]
            )

    def outcome(self):
        """Outcome of this sub suite.

        :return: Test suite outcome from the test suite finished event.
        :rtype: dict
        """
        if self.finished:
            return self.test_suite_finished.get("data", {}).get("testSuiteOutcome", {})
        return {}

    def start(self, identifier):
        """Start ETR for this sub suite.

        :param identifier: An identifier for logs in this sub suite.
        :type identifier: str
        """
        FORMAT_CONFIG.identifier = identifier
        self.logger.info("Triggering ETR.")
        executor = Executor(self.etos)
        executor.run_tests(self.environment)
        self.logger.info("ETR triggered.")
        timeout = time.time() + self.etos.debug.default_test_result_timeout
        try:
            while time.time() < timeout:
                time.sleep(10)
                if not self.started:
                    continue
                self.logger.info("ETR started.")
                self.request_finished_event()
                if self.finished:
                    self.logger.info("ETR finished.")
                    break
        finally:
            self.release()

    def release(self):
        """Release this sub suite."""
        self.logger.info("Releasing environment")
        wait_generator = self.etos.http.wait_for_request(
            self.etos.debug.environment_provider,
            params={"single_release": self.environment["id"]},
            timeout=60,
        )
        for response in wait_generator:
            if response:
                self.logger.info("Successfully released")
                self.released = True
                break


class TestSuite:  # pylint:disable=too-many-instance-attributes
    """Handle the starting and waiting for test suites in ETOS."""

    test_suite_started = None
    started = False
    empty = False
    __activity_triggered = None
    __activity_finished = None

    def __init__(self, etos, params, suite):
        """Initialize a TestSuite instance."""
        self.etos = etos
        self.params = params
        self.suite = suite
        self.logger = logging.getLogger(f"TestSuite - {self.suite.get('name')}")
        self.logger.addFilter(DuplicateFilter(self.logger))
        self.sub_suites = []

    @property
    def sub_suite_environments(self):
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
    def all_finished(self):
        """Whether or not all sub suites are finished."""
        return all(sub_suite.finished for sub_suite in self.sub_suites)

    def __environment_activity_triggered(self, test_suite_started_id):
        """Activity triggered event from the environment provider.

        :param test_suite_started_id: The ID that the activity triggered links to.
        :type test_suite_started_id: str
        :return: An activity triggered event dictionary.
        :rtype: dict
        """
        if self.__activity_triggered is None:
            self.__activity_triggered = request_activity_triggered(self.etos, test_suite_started_id)
        return self.__activity_triggered

    def __environment_activity_finished(self, activity_triggered_id):
        """Activity finished event from the environment provider.

        :param activity_triggered_id: The ID that the activity finished links to.
        :type activity_triggered_id: str
        :return: An activity finished event dictionary.
        :rtype: dict
        """
        if self.__activity_finished is None:
            self.__activity_finished = request_activity_finished(self.etos, activity_triggered_id)
        return self.__activity_finished

    def _download_sub_suite(self, environment):
        """Download a sub suite from an EnvironmentDefined event.

        :param environment: Environment defined event to download from.
        :type environment: dict
        :return: Downloaded sub suite information.
        :rtype: dict
        """
        if environment["data"].get("uri") is None:
            return None
        uri = environment["data"]["uri"]
        json_header = {"Accept": "application/json"}
        json_response = self.etos.http.wait_for_request(
            uri,
            headers=json_header,
        )
        suite = {}
        for suite in json_response:
            break
        else:
            raise Exception("Could not download sub suite instructions")
        return suite

    def _announce(self, header, body):
        """Send an announcement over Eiffel.

        :param header: Header of the announcement.
        :type header: str
        :param body: Body of the announcement.
        :type body: str
        """
        self.etos.events.send_announcement_published(
            f"[ESR] {header}",
            body,
            "MINOR",
            {"CONTEXT": self.etos.config.get("context")},
        )

    def _send_test_suite_started(self):
        """Send a test suite started event.

        :return: Test suite started event.
        :rtype: :obj:`eiffellib.events.EiffelTestSuiteStartedEvent`
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
        links = {"CONTEXT": self.etos.config.get("context")}
        return self.etos.events.send(test_suite_started, links, data)

    def start(self):
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
            self.logger.info("Waiting for all sub suite environments")
            for sub_suite_environment in self.sub_suite_environments:
                sub_suite_definition = self._download_sub_suite(sub_suite_environment)
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
            self.logger.info("All sub suite environments received and sub suites triggered")

            self.logger.info("All %d sub suites triggered", len(self.sub_suites))
            self.started = True
        finally:
            for thread in threads:
                thread.join()
        self.logger.info("All %d sub suites finished", len(self.sub_suites))

    def release_all(self):
        """Release all, unreleased, sub suites."""
        self.logger.info("Releasing all sub suite environments")
        for sub_suite in self.sub_suites:
            if not sub_suite.released:
                sub_suite.release()
        self.logger.info("All sub suite environments are released")

    def finish(self, verdict, conclusion, description):
        """Send test suite finished for this test suite.

        :param verdict: Verdict of the execution.
        :type verdict: str
        :param conclusion: Conclusion taken on the results.
        :type conclusion: str
        :param description: Description of the verdict and conclusion.
        :type description: str
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

    def results(self):
        """Test results for this execution.

        :return: Verdict, conclusion and description.
        :rtype: tuple
        """
        verdict = "INCONCLUSIVE"
        conclusion = "SUCCESSFUL"
        description = ""

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
        return verdict, conclusion, description
