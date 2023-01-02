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
"""Scenario tests for the most regular cases."""
import os
import time
import json
import logging
from unittest import TestCase
from functools import partial

from etos_lib.lib.debug import Debug
from etos_lib.lib.config import Config

from etos_suite_runner.esr import ESR

from tests.scenario.tercc import TERCC, TERCC_SUB_SUITES, TERCC_EMPTY
from tests.library.fake_server import FakeServer
from tests.library.handler import Handler


class TestRegularScenario(TestCase):
    """Regular test scenarios using 1 suite with 1 sub suite."""

    logger = logging.getLogger(__name__)

    def setUp(self):
        """Set up environment variables for the ESR."""
        os.environ["ETOS_DISABLE_SENDING_EVENTS"] = "1"
        os.environ["ESR_WAIT_FOR_ENVIRONMENT_TIMEOUT"] = "20"
        os.environ["SUITE_RUNNER"] = "registry.nordix.org/eiffel/etos-suite-runner"
        os.environ["SOURCE_HOST"] = "localhost"

    def tearDown(self):
        """Reset all globally stored data for the next test."""
        Handler.reset()
        Config().reset()
        # pylint:disable=protected-access
        Debug()._Debug__events_published.clear()
        Debug()._Debug__events_received.clear()

    def validate_event_name_order(self, events):
        """Validate ESR sent events.

        :raises AssertionError: If events are not correct.

        :param events: All events sent, in order.
        :type events: deque
        """
        self.logger.info(events)
        event_names_any_order = [
            "EiffelAnnouncementPublishedEvent",
            "EiffelAnnouncementPublishedEvent",
        ]
        event_names_in_order = [
            "EiffelActivityTriggeredEvent",
            "EiffelEnvironmentDefinedEvent",
            "EiffelActivityStartedEvent",
            "EiffelTestSuiteStartedEvent",
            "EiffelTestSuiteFinishedEvent",
            "EiffelActivityFinishedEvent",
        ]
        for event_name in event_names_in_order:
            sent_event = events.popleft().meta.type
            while sent_event in event_names_any_order:
                event_names_any_order.remove(sent_event)
                sent_event = events.popleft().meta.type
            self.assertEqual(sent_event, event_name)
        self.assertEqual(list(events), [])

    def test_full_scenario(self):
        """Test ESR using 1 suite with 1 sub suite.

        Approval criteria:
            - It shall be possible to execute a full scenario in ESR without failures.
            - The ESR shall send  events in the correct order.

        Test steps:
            1. Start up a fake server.
            2. Initialize and run ESR.
            3. Verify that the ESR executes without errors.
            4. Verify that all events were sent and in the correct order.
        """
        os.environ["TERCC"] = json.dumps(TERCC)
        tercc = json.loads(os.environ["TERCC"])

        handler = partial(Handler, tercc)
        end = time.time() + 25
        self.logger.info("STEP: Start up a fake server.")
        with FakeServer(handler) as server:
            os.environ["ETOS_GRAPHQL_SERVER"] = server.host
            os.environ["ETOS_ENVIRONMENT_PROVIDER"] = server.host

            self.logger.info("STEP: Initialize and run ESR.")
            esr = ESR()

            try:
                self.logger.info("STEP: Verify that the ESR executes without errors.")
                esr.run()

                self.logger.info("STEP: Verify that all events were sent and in the correct order.")
                self.validate_event_name_order(Debug().events_published.copy())
            finally:
                # If the _get_environment_status method in ESR does not time out before the test
                # finishes there will be loads of tracebacks in the log. Won't fail the test but
                # the noise is immense.
                while time.time() <= end:
                    time.sleep(1)

    def test_full_scenario_sub_suites(self):
        """Test ESR using 1 suite with 2 sub suites.

        Approval criteria:
            - It shall be possible to execute a full scenario with sub suites in ESR without
              failures.
            - The ESR shall send  events in the correct order.

        Test steps:
            1. Start up a fake server.
            2. Initialize and run ESR.
            3. Verify that the ESR executes without errors.
            4. Verify that all events were sent and in the correct order.
        """
        os.environ["TERCC"] = json.dumps(TERCC_SUB_SUITES)
        tercc = json.loads(os.environ["TERCC"])

        handler = partial(Handler, tercc)
        end = time.time() + 25
        self.logger.info("STEP: Start up a fake server.")
        with FakeServer(handler) as server:
            os.environ["ETOS_GRAPHQL_SERVER"] = server.host
            os.environ["ETOS_ENVIRONMENT_PROVIDER"] = server.host

            self.logger.info("STEP: Initialize and run ESR.")
            esr = ESR()

            try:
                self.logger.info("STEP: Verify that the ESR executes without errors.")
                esr.run()

                self.logger.info("STEP: Verify that all events were sent and in the correct order.")
                self.validate_event_name_order(Debug().events_published.copy())
            finally:
                # If the _get_environment_status method in ESR does not time out before the test
                # finishes there will be loads of tracebacks in the log. Won't fail the test but
                # the noise is immense.
                while time.time() <= end:
                    time.sleep(1)

    def test_esr_without_recipes(self):
        """Test ESR using 1 suite without any recipes.

        Approval criteria:
            - ESR shall exit early if there are no recipes to test.

        Test steps:
            1. Start up a fake server.
            2. Initialize and run ESR.
            3. Verify that the ESR exits.
        """
        os.environ["TERCC"] = json.dumps(TERCC_EMPTY)
        tercc = json.loads(os.environ["TERCC"])

        handler = partial(Handler, tercc)
        end = time.time() + 25
        self.logger.info("STEP: Start up a fake server.")
        with FakeServer(handler) as server:
            os.environ["ETOS_GRAPHQL_SERVER"] = server.host
            os.environ["ETOS_ENVIRONMENT_PROVIDER"] = server.host

            self.logger.info("STEP: Initialize and run ESR.")
            esr = ESR()
            try:
                esr.run()
                finished = None
                for event in Debug().events_published.copy():
                    if event.meta.type == "EiffelTestSuiteFinishedEvent":
                        finished = event
                        break
                self.logger.info("STEP: Verify that the ESR exits.")
                assert (
                    finished is not None
                ), "EiffelTestSuiteFinished was not sent when test suite is empty"
                outcome = finished.json.get("data", {}).get("outcome", {})
                assert (
                    outcome.get("conclusion") == "FAILED"
                ), "Conclusion was not FAILED when test suite is empty"
                assert (
                    outcome.get("verdict") == "INCONCLUSIVE"
                ), "Verdict was not INCONCLUSIVE when test suite is empty"
            finally:
                # If the _get_environment_status method in ESR does not time out before the test
                # finishes there will be loads of tracebacks in the log. Won't fail the test but
                # the noise is immense.
                while time.time() <= end:
                    time.sleep(1)
