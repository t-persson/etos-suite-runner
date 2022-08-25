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
import time
from threading import Lock, Thread
from multiprocessing.pool import ThreadPool

from etos_lib.logging.logger import FORMAT_CONFIG
from eiffellib.events.eiffel_test_suite_started_event import EiffelTestSuiteStartedEvent

from etos_suite_runner.lib.result_handler import ResultHandler
from etos_suite_runner.lib.executor import Executor
from etos_suite_runner.lib.exceptions import EnvironmentProviderException
from etos_suite_runner.lib.graphql import (
    request_environment_defined,
)


class SuiteRunner:  # pylint:disable=too-few-public-methods
    """Test suite runner.

    Splits test suites into sub suites based on number of products available.
    Starts ETOS test runner (ETR) and sends out a test suite finished.
    """

    lock = Lock()
    environment_provider_done = False
    error = False
    logger = logging.getLogger("ESR - Runner")

    def __init__(self, params, etos, context):
        """Initialize.

        :param params: Parameters object for this suite runner.
        :type params: :obj:`etos_suite_runner.lib.esr_parameters.ESRParameters`
        :param etos: ETOS library object.
        :type etos: :obj:`etos_lib.etos.ETOS`
        :param context: Context which triggered the runner.
        :type context: str
        """
        self.params = params
        self.etos = etos
        self.context = context
        self.sub_suites = {}

    def _release_environment(self, task_id):
        """Release an environment from the environment provider.

        :param task_id: Task ID to release.
        :type task_id: str
        """
        wait_generator = self.etos.http.wait_for_request(
            self.etos.debug.environment_provider, params={"release": task_id}
        )
        for response in wait_generator:
            if response:
                break

    def _run_etr(self, environment):
        """Trigger an instance of ETR.

        :param environment: Environment which to execute in.
        :type environment: dict
        """
        executor = Executor(self.etos)
        executor.run_tests(environment)

    def _environments(self):
        """Get environments for all test suites in this ETOS run."""
        FORMAT_CONFIG.identifier = self.params.tercc.meta.event_id
        downloaded = []
        status = {
            "status": "FAILURE",
            "error": "Couldn't collect any error information",
        }
        timeout = time.time() + self.etos.config.get("WAIT_FOR_ENVIRONMENT_TIMEOUT")
        while time.time() < timeout:
            status = self.params.environment_status
            self.logger.info(status)
            for environment in request_environment_defined(self.etos, self.context):
                if environment["meta"]["id"] in downloaded:
                    continue
                suite = self._download_sub_suite(environment)
                if self.error:
                    break
                downloaded.append(environment["meta"]["id"])
                if suite is None:  # Not a real sub suite environment defined event.
                    continue
                with self.lock:
                    self.sub_suites.setdefault(suite["test_suite_started_id"], [])
                    self.sub_suites[suite["test_suite_started_id"]].append(suite)
            # We must have found at least one environment for each test suite.
            if status["status"] != "PENDING" and len(downloaded) >= len(
                self.params.test_suite
            ):
                self.environment_provider_done = True
                break
            time.sleep(5)
        if status["status"] == "FAILURE":
            self.error = EnvironmentProviderException(
                status["error"], self.etos.config.get("task_id")
            )

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
            self.error = Exception("Could not download sub suite instructions")
        return suite

    def _sub_suites(self, main_suite_id):
        """Get all sub suites that correlates with ID.

        :param main_suite_id: Main suite ID to correlate sub suites to.
        :type main_suite_id: str
        :return: Each correlated sub suite.
        :rtype: Iterator
        """
        while not self.error:
            downloaded_all = self.environment_provider_done
            time.sleep(1)
            with self.lock:
                sub_suites = self.sub_suites.get(main_suite_id, []).copy()
            for sub_suite in sub_suites:
                with self.lock:
                    self.sub_suites[main_suite_id].remove(sub_suite)
                yield sub_suite
            if downloaded_all:
                break

    def start_sub_suites(self, suite):
        """Start up all sub suites within a TERCC suite.

        :param suite: TERCC suite to start up sub suites from.
        :type suite: dict
        """
        suite_name = suite.get("name")
        self.etos.events.send_announcement_published(
            "[ESR] Starting tests.",
            "Starting test suites on all checked out IUTs.",
            "MINOR",
            {"CONTEXT": self.context},
        )
        self.logger.info("Starting sub suites for %r", suite_name)
        started = []
        for sub_suite in self._sub_suites(suite["test_suite_started_id"]):
            started.append(sub_suite)

            self.logger.info("Triggering sub suite %r", sub_suite["name"])
            self._run_etr(sub_suite)
            self.logger.info("%r Triggered", sub_suite["name"])
            time.sleep(1)
        self.logger.info("All %d sub suites for %r started", len(started), suite_name)

        self.etos.events.send_announcement_published(
            "[ESR] Waiting.",
            "Waiting for test suites to finish",
            "MINOR",
            {"CONTEXT": self.context},
        )
        return started

    def start_suite(self, suite):
        """Send test suite events and launch test runners.

        :param suite: Test suite to start.
        :type suite: dict
        """
        FORMAT_CONFIG.identifier = self.params.tercc.meta.event_id
        suite_name = suite.get("name")
        self.logger.info("Starting %s.", suite_name)

        categories = ["Regression test suite"]
        if self.params.product:
            categories.append(self.params.product)

        test_suite_started = EiffelTestSuiteStartedEvent()

        # This ID has been stored in Environment so that the ETR know which test suite to link to.
        test_suite_started.meta.event_id = suite.get("test_suite_started_id")
        data = {"name": suite_name, "categories": categories, "types": ["FUNCTIONAL"]}
        links = {"CONTEXT": self.context}
        self.etos.events.send(test_suite_started, links, data)

        verdict = "INCONCLUSIVE"
        conclusion = "INCONCLUSIVE"
        description = ""

        result_handler = ResultHandler(self.etos, test_suite_started)
        try:
            started = self.start_sub_suites(suite)
            self.logger.info("Wait for test results.")
            result_handler.wait_for_test_suite_finished(len(started))
            verdict, conclusion, description = result_handler.test_results()
            time.sleep(5)
        except Exception as exc:
            conclusion = "FAILED"
            description = str(exc)
            raise
        finally:
            self.etos.events.send_test_suite_finished(
                test_suite_started,
                {"CONTEXT": self.context},
                outcome={
                    "verdict": verdict,
                    "conclusion": conclusion,
                    "description": description,
                },
            )
            # TODO: Add releasing of environment defined IDs when that is supported
        self.logger.info("Test suite finished.")

    def start_suites_and_wait(self):
        """Get environments and start all test suites."""
        Thread(target=self._environments, daemon=True).start()
        try:
            with ThreadPool() as pool:
                pool.map(self.start_suite, self.params.test_suite)
            if self.error:
                raise self.error
        finally:
            task_id = self.etos.config.get("task_id")
            self.logger.info("Release test environment.")
            if task_id is not None:
                self._release_environment(task_id)
