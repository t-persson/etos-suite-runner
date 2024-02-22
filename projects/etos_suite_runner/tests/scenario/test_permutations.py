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
"""Scenario tests for permutations."""
import json
import logging
import os
import time
from copy import deepcopy
from functools import partial
from unittest import TestCase

from eiffellib.events import (
    EiffelArtifactCreatedEvent,
    EiffelTestExecutionRecipeCollectionCreatedEvent,
)
from etos_lib.lib.config import Config
from etos_lib.lib.debug import Debug
from etos_suite_runner.esr import ESR
from tests.library.fake_database import FakeDatabase
from tests.library.fake_server import FakeServer
from tests.library.handler import Handler
from tests.scenario.tercc import ARTIFACT_CREATED, PERMUTATION_TERCC, PERMUTATION_TERCC_SUB_SUITES

IUT_PROVIDER = {
    "iut": {
        "id": "default",
        "list": {
            "possible": {
                "$expand": {
                    "value": {
                        "type": "$identity.type",
                        "namespace": "$identity.namespace",
                        "name": "$identity.name",
                        "version": "$identity.version",
                        "qualifiers": "$identity.qualifiers",
                        "subpath": "$identity.subpath",
                    },
                    "to": "$amount",
                }
            },
            "available": "$this.possible",
        },
    }
}

EXECUTION_SPACE_PROVIDER = {
    "execution_space": {
        "id": "default",
        "list": {
            "possible": {
                "$expand": {
                    "value": {
                        "instructions": "$execution_space_instructions",
                        "request": {
                            "url": {"$join": {"strings": ["$dataset.host", "/etr"]}},
                            "method": "POST",
                            "json": "$expand_value.instructions",
                        },
                    },
                    "to": "$amount",
                }
            },
            "available": "$this.possible",
        },
    }
}

LOG_AREA_PROVIDER = {
    "log": {
        "id": "default",
        "list": {
            "possible": {
                "$expand": {
                    "value": {
                        "upload": {
                            "url": {"$join": {"strings": ["$dataset.host", "/log/", "{name}"]}},
                            "method": "POST",
                        }
                    },
                    "to": "$amount",
                }
            },
            "available": "$this.possible",
        },
    }
}


class TestPermutationScenario(TestCase):
    """Permutation test scenario using 2 suites with 1 sub suite each."""

    logger = logging.getLogger(__name__)

    def setUp(self):
        """Set up environment variables for the ESR."""
        os.environ["ETOS_DISABLE_SENDING_EVENTS"] = "1"
        os.environ["ESR_WAIT_FOR_ENVIRONMENT_TIMEOUT"] = "20"
        os.environ["SUITE_RUNNER"] = "registry.nordix.org/eiffel/etos-suite-runner"
        os.environ["SOURCE_HOST"] = "localhost"
        Config().set("database", FakeDatabase())

    def tearDown(self):
        """Reset all globally stored data for the next test."""
        Handler.reset()
        Config().reset()
        # pylint:disable=protected-access
        Debug()._Debug__events_published.clear()
        Debug()._Debug__events_received.clear()

    def setup_providers(self):
        """Setup providers in the fake ETCD database."""
        Config().get("database").put("/environment/provider/iut/default", json.dumps(IUT_PROVIDER))
        Config().get("database").put(
            "/environment/provider/execution-space/default", json.dumps(EXECUTION_SPACE_PROVIDER)
        )
        Config().get("database").put(
            "/environment/provider/log-area/default", json.dumps(LOG_AREA_PROVIDER)
        )

    def register_providers(self, testrun_id, host):
        """Register providers for a testrun in the fake ETCD database.

        :param testrun_id: ID to set up providers for.
        :type testrun_id: str
        :param host: The host to set in the dataset.
        :type host: str
        """
        Config().get("database").put(
            f"/testrun/{testrun_id}/provider/iut", json.dumps(IUT_PROVIDER)
        )
        Config().get("database").put(
            f"/testrun/{testrun_id}/provider/log-area", json.dumps(LOG_AREA_PROVIDER)
        )
        Config().get("database").put(
            f"/testrun/{testrun_id}/provider/execution-space", json.dumps(EXECUTION_SPACE_PROVIDER)
        )
        Config().get("database").put(
            f"/testrun/{testrun_id}/provider/dataset", json.dumps({"host": host})
        )

    def publish_initial_events(self, tercc, artc):
        """Publish events that are required for the ESR to start.

        :param tercc: The test execution recipe collection created event that triggered the ESR.
        :type tercc: dict
        :param artc: The IUT artifact that the tercc links to.
        :type artc: dict
        """
        tercc_event = EiffelTestExecutionRecipeCollectionCreatedEvent()
        tercc_event.rebuild(deepcopy(tercc))
        artc_event = EiffelArtifactCreatedEvent()
        artc_event.rebuild(deepcopy(artc))
        Debug().events_published.append(artc_event)
        Debug().events_published.append(tercc_event)

    def test_permutation_scenario(self):
        """Test permutations using 2 suites with 1 sub suite each.

        Approval criteria:
            - It shall be possible to execute permutations in ESR without failures.

        Test steps:
            1. Start up a fake server.
            2. Initialize and run ESR.
            3. Verify that the ESR executes without errors.
        """
        os.environ["TERCC"] = json.dumps(PERMUTATION_TERCC)
        tercc = json.loads(os.environ["TERCC"])
        testrun_id = tercc["meta"]["id"]
        self.setup_providers()
        self.publish_initial_events(tercc, ARTIFACT_CREATED)

        handler = partial(Handler, tercc)
        end = time.time() + 25
        self.logger.info("STEP: Start up a fake server.")
        with FakeServer(handler) as server:
            self.register_providers(testrun_id, server.host)
            os.environ["ETOS_GRAPHQL_SERVER"] = server.host
            os.environ["ETOS_ENVIRONMENT_PROVIDER"] = server.host
            os.environ["ETOS_API"] = server.host

            self.logger.info("STEP: Initialize and run ESR.")
            esr = ESR()

            try:
                self.logger.info("STEP: Verify that the ESR executes without errors.")
                suite_ids = esr.run()

                self.assertEqual(len(suite_ids), 2, "There shall only be two test suite started.")
                for suite_id in suite_ids:
                    suite_finished = Handler.get_from_db(
                        "EiffelTestSuiteFinishedEvent", {"links.target": suite_id}
                    )
                    self.assertEqual(
                        len(suite_finished), 1, "There shall only be a single test suite finished."
                    )
                    outcome = suite_finished[0].get("data", {}).get("outcome", {})
                    self.logger.info(outcome)
                    self.assertDictEqual(
                        outcome,
                        {
                            "conclusion": "SUCCESSFUL",
                            "verdict": "PASSED",
                            "description": "All tests passed.",
                        },
                        f"Wrong outcome {outcome!r}, outcome should be successful.",
                    )
            finally:
                # If the _get_environment_status method in ESR does not time out before the test
                # finishes there will be loads of tracebacks in the log. Won't fail the test but
                # the noise is immense.
                while time.time() <= end:
                    status = esr.params.get_status()
                    if status["status"] != "NOT_STARTED":
                        break
                    time.sleep(1)

    def test_permutation_scenario_sub_suites(self):
        """Test permutations using 2 suites with 2 sub suite each.

        Approval criteria:
            - It shall be possible to execute permutations in ESR without failures.

        Test steps:
            1. Start up a fake server.
            2. Initialize and run ESR.
            3. Verify that the ESR executes without errors.
        """
        os.environ["TERCC"] = json.dumps(PERMUTATION_TERCC_SUB_SUITES)
        tercc = json.loads(os.environ["TERCC"])
        testrun_id = tercc["meta"]["id"]
        self.setup_providers()
        self.publish_initial_events(tercc, ARTIFACT_CREATED)

        handler = partial(Handler, tercc)
        end = time.time() + 25
        self.logger.info("STEP: Start up a fake server.")
        with FakeServer(handler) as server:
            self.register_providers(testrun_id, server.host)
            os.environ["ETOS_GRAPHQL_SERVER"] = server.host
            os.environ["ETOS_ENVIRONMENT_PROVIDER"] = server.host
            os.environ["ETOS_API"] = server.host

            self.logger.info("STEP: Initialize and run ESR.")
            esr = ESR()

            try:
                self.logger.info("STEP: Verify that the ESR executes without errors.")
                suite_ids = esr.run()

                self.assertEqual(
                    len(suite_ids), 2, "There shall only be a single test suite started."
                )
                for suite_id in suite_ids:
                    suite_finished = Handler.get_from_db(
                        "EiffelTestSuiteFinishedEvent", {"links.target": suite_id}
                    )
                    self.assertEqual(
                        len(suite_finished), 1, "There shall only be a single test suite finished."
                    )
                    outcome = suite_finished[0].get("data", {}).get("outcome", {})
                    self.logger.info(outcome)
                    self.assertDictEqual(
                        outcome,
                        {
                            "conclusion": "SUCCESSFUL",
                            "verdict": "PASSED",
                            "description": "All tests passed.",
                        },
                        f"Wrong outcome {outcome!r}, outcome should be successful.",
                    )

            finally:
                # If the _get_environment_status method in ESR does not time out before the test
                # finishes there will be loads of tracebacks in the log. Won't fail the test but
                # the noise is immense.
                while time.time() <= end:
                    status = esr.params.get_status()
                    if status["status"] != "NOT_STARTED":
                        break
                    time.sleep(1)
