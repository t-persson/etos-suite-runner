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
"""ETOS suite runner result handler module."""
import time
import logging
from .log_filter import DuplicateFilter
from .graphql import (
    request_activity,
    request_test_suite_finished,
    request_test_suite_started,
)


class ResultHandler:
    """ESR test result handler."""

    activity_id = None
    logger = logging.getLogger("ESR - ResultHandler")

    def __init__(self, etos):
        """ESR test result handler."""
        self.etos = etos
        self.events = {}

    @property
    def has_started(self):
        """Whether or not test suites have started."""
        expected_number_of_suites = self.etos.config.get("nbr_of_suites")
        return len(self.events.get("subSuiteStarted", [])) == expected_number_of_suites

    @property
    def has_finished(self):
        """Whether or not test suites have finished.

        :return: If tests have started and there are subSuitesFinished equal to the
                 number of expected test suites, then we return True.
        :rtype: bool
        """
        if not self.has_started:
            return False
        if not self.events.get("subSuiteFinished"):
            return False
        nbr_of_finished = len(self.events.get("subSuiteFinished"))
        expected_number_of_suites = self.etos.config.get("nbr_of_suites")
        return nbr_of_finished == expected_number_of_suites

    @property
    def test_suites_finished(self):
        """Iterate over all finished test suites."""
        for test_suite_finished in self.events.get("subSuiteFinished"):
            yield test_suite_finished

    def test_results(self):
        """Test results for the ESR execution.

        :return: Verdict, conclusion and description.
        :rtype: tuple
        """
        verdict = "INCONCLUSIVE"
        conclusion = "SUCCESSFUL"
        description = ""

        if self.etos.config.get("results") is None:
            verdict = "INCONCLUSIVE"
            conclusion = "FAILED"
            description = "Did not receive test results from sub suites."
        else:
            for test_suite_finished in self.test_suites_finished:
                data = test_suite_finished.get("data", {})
                outcome = data.get("testSuiteOutcome", {})
                if outcome.get("verdict") != "PASSED":
                    verdict = "FAILED"
                description = outcome.get("description")

            # If we get this far without exceptions or return statements
            # and the verdict is still inconclusive, it would mean that
            # that we passed everything.
            if verdict == "INCONCLUSIVE":
                description = "All tests passed."
                verdict = "PASSED"
            if not description:
                description = "No description received from ESR or ETR."
        return verdict, conclusion, description

    def get_events(self, suite_id):
        """Get events from an activity started from suite id.

        :param suite_id: ID of test execution recipe that triggered this activity.
        :type suite_id: str
        :return: Dictionary of all events generated for this suite.
        :rtype: dict
        """
        self.logger.info("Requesting events from GraphQL")
        if self.activity_id is None:
            self.logger.info("Getting activity ID.")
            activity = request_activity(self.etos, suite_id)
            if activity is None:
                self.logger.warning("Activity ID not found yet.")
                return
            self.logger.info("Activity id: %r", activity["meta"]["id"])
            self.activity_id = activity["meta"]["id"]

        expected_number_of_suites = self.etos.config.get("nbr_of_suites")
        self.logger.info("Expected number of suites: %r", expected_number_of_suites)

        events = {
            "subSuiteStarted": [],
            "subSuiteFinished": [],
        }
        main_suite = self.etos.config.get("test_suite_started")
        self.logger.info("Main suite: %r", main_suite["meta"]["id"])
        if len(self.events.get("subSuiteStarted", [])) != expected_number_of_suites:
            self.logger.info("Getting subSuiteStarted")
            started = [
                test_suite_started
                for test_suite_started in request_test_suite_started(
                    self.etos, self.activity_id
                )
                if test_suite_started["meta"]["id"] != main_suite["meta"]["id"]
            ]
            if not started:
                self.logger.info("No subSuitesStarted yet.")
                self.events = events
                return
            self.logger.info("Found: %r", len(started))
            self.events["subSuiteStarted"] = started
        events["subSuiteStarted"] = self.events.get("subSuiteStarted", [])

        started = self.events.get("subSuiteStarted", [])
        started_ids = [
            test_suite_started["meta"]["id"] for test_suite_started in started
        ]
        if len(self.events.get("subSuiteFinished", [])) != expected_number_of_suites:
            self.logger.info("Getting subSuiteFinished")
            finished = list(request_test_suite_finished(self.etos, started_ids))
            if not finished:
                self.logger.info("No subSuiteFinished yet.")
                self.events = events
                return
            self.logger.info("Found: %r", len(finished))
            self.events["subSuiteFinished"] = finished
        events["subSuiteFinished"] = self.events.get("subSuiteFinished", [])
        self.events = events

    def wait_for_test_suite_finished(self):
        """Wait for test suites to finish."""
        tercc = self.etos.config.get("tercc")

        timeout = time.time() + self.etos.debug.default_test_result_timeout
        print_once = False
        with DuplicateFilter(self.logger):
            while time.time() < timeout:
                time.sleep(10)
                self.get_events(tercc.meta.event_id)
                expected_number_of_suites = self.etos.config.get("nbr_of_suites")
                self.logger.info(
                    "Expected number of test suites: %r, currently active: %r",
                    expected_number_of_suites,
                    len(self.events.get("subSuiteStarted", [])),
                )
                if not self.has_started:
                    continue
                if not print_once:
                    print_once = True
                    self.logger.info("Test suites have started.")
                if self.has_finished:
                    self.logger.info("Test suites have finished.")
                    self.etos.config.set("results", self.events)
                    return True

                self.logger.info("Waiting for test suites to finish.")
        return False
