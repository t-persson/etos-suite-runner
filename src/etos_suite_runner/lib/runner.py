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
from threading import Thread
from multiprocessing.pool import ThreadPool

from etos_lib.logging.logger import FORMAT_CONFIG

from .suite import TestSuite


class SuiteRunner:  # pylint:disable=too-few-public-methods
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

    def _release_environment(self, task_id):
        """Release an environment from the environment provider.

        :param task_id: Task ID to release.
        :type task_id: str
        """
        wait_generator = self.etos.http.wait_for_request(
            self.etos.debug.environment_provider, params={"release": task_id}, timeout=60
        )
        for response in wait_generator:
            if response:
                break

    def start_suites_and_wait(self):
        """Get environments and start all test suites."""
        Thread(target=self.params.collect_environments, daemon=True).start()
        try:
            test_suites = [
                TestSuite(self.etos, self.params, suite) for suite in self.params.test_suite
            ]
            with ThreadPool() as pool:
                pool.map(self.run, test_suites)
            if self.params.error:
                raise self.params.error
        finally:
            task_id = self.etos.config.get("task_id")
            self.logger.info("Release the full test environment.")
            if task_id is not None:
                self._release_environment(task_id)

    def run(self, test_suite):
        """Run test suite runner.

        :param test_suite: Test suite to run.
        :type test_suite: :obj:`TestSuite`
        """
        FORMAT_CONFIG.identifier = self.params.tercc.meta.event_id
        try:
            test_suite.start()  # send EiffelTestSuiteStartedEvent
            # All sub suites finished.
        finally:
            results = test_suite.results()
            test_suite.finish(*results)  # send EiffelTestSuiteFinishedEvent
            test_suite.release_all()
