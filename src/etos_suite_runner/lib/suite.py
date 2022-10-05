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
from .graphql import request_test_suite_finished, request_test_suite_started
from .log_filter import DuplicateFilter


class SubSuite:
    """Handle test results and tracking of a single sub suite."""

    released = False

    def __init__(self, etos, environment):
        """Initialize a sub suite."""
        self.etos = etos
        self.environment = environment
        self.name = self.environment.get("name")
        self.logger = logging.getLogger(f"SubSuite - {self.name}")
        self.logger.addFilter(DuplicateFilter(self.logger))
        self.test_suite_started = {}  # This is set by a different thread.
        self.test_suite_finished = {}

    @property
    def finished(self):
        """Whether or not this sub suite has finished."""
        return bool(self.test_suite_finished)

    @property
    def started(self):
        """Whether or not this sub suite has started."""
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
                time.sleep(1)
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


class TestSuite:
    """Handle the starting and waiting for test suites in ETOS."""

    test_suite_started = None
    started = False
    lock = threading.Lock()

    def __init__(self, etos, params, suite):
        """Initialize a TestSuite instance."""
        self.etos = etos
        self.params = params
        self.suite = suite
        self.logger = logging.getLogger(f"TestSuite - {self.suite.get('name')}")
        self.logger.addFilter(DuplicateFilter(self.logger))
        self.sub_suites = []

    @property
    def sub_suite_definitions(self):
        """All sub suite definitions from the environment provider.

        Each sub suite definition is an environment for the sub suites to execute in.
        """
        yield from self.params.environments(self.suite["test_suite_started_id"])

    @property
    def all_finished(self):
        """Whether or not all sub suites are finished."""
        with self.lock:
            return all(sub_suite.finished for sub_suite in self.sub_suites)

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

        self.logger.info("Starting sub suites")
        threads = []
        assigner = None
        try:
            self.logger.info("Waiting for all sub suite environments")
            for sub_suite_definition in self.sub_suite_definitions:
                sub_suite = SubSuite(self.etos, sub_suite_definition)
                with self.lock:
                    self.sub_suites.append(sub_suite)
                thread = threading.Thread(
                    target=sub_suite.start, args=(self.params.tercc.meta.event_id,)
                )
                threads.append(thread)
                thread.start()
            self.logger.info("All sub suite environments received and sub suites triggered")

            self.logger.info("Assigning test suite started events to sub suites")
            assigner = threading.Thread(target=self._assign_test_suite_started)
            assigner.start()

            if self.params.error:
                self.logger.error("Environment provider error: %r", self.params.error)
                self._announce(
                    "Error",
                    f"Environment provider failed to provide an environment: '{self.params.error}'"
                    "\nWill finish already started sub suites\n",
                )
                return
            with self.lock:
                number_of_suites = len(self.sub_suites)
            self.logger.info("All %d sub suites triggered", number_of_suites)
            self.started = True
        finally:
            if assigner is not None:
                assigner.join()
            for thread in threads:
                thread.join()
        self.logger.info("All %d sub suites finished", number_of_suites)

    def _assign_test_suite_started(self):
        """Assign test suite started events to all sub suites."""
        FORMAT_CONFIG.identifier = self.params.tercc.meta.event_id
        timeout = time.time() + self.etos.debug.default_test_result_timeout
        self.logger.info("Assigning test suite started to sub suites")
        # Number of TestSuiteStarted assigned to :obj:`SubSuite` instances.
        number_of_assigned = 0
        while time.time() < timeout:
            time.sleep(1)
            suites = []
            with self.lock:
                sub_suites = self.sub_suites.copy()
            if len(sub_suites) == 0 and self.params.error:
                self.logger.info("Environment provider error")
                return
            if len(sub_suites) == 0:
                self.logger.info("No sub suites started just yet")
                continue
            for test_suite_started in request_test_suite_started(
                self.etos, self.suite["test_suite_started_id"]
            ):
                self.logger.info("Found test suite started")
                suites.append(test_suite_started)
                for sub_suite in sub_suites:
                    self.logger.info("SubSuite        : %s", sub_suite.name)
                    self.logger.info("TestSuiteStarted: %s", test_suite_started["data"]["name"])
                    if sub_suite.started:
                        continue
                    # Using name to match here is safe because we're only searching for
                    # sub suites that are connected to this test_suite_started ID and the
                    # "_SubSuite_\d" part of the name is set by ETOS and not humans.
                    if sub_suite.name == test_suite_started["data"]["name"]:
                        number_of_assigned += 1
                        self.logger.info("Test suite started assigned to %r", sub_suite.name)
                        sub_suite.test_suite_started = test_suite_started
                    else:
                        self.logger.info(
                            "No assigned test suite started for %r",
                            test_suite_started["data"]["name"],
                        )
            if number_of_assigned == 0:
                self.logger.info("Found no test suite started to assign to sub suites yet")
                continue
            if len(suites) == len(sub_suites) and len(sub_suites) == number_of_assigned:
                self.logger.info("All %d sub suites started", len(sub_suites))
                break

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

        if not self.started:
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
