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
"""ETOS suite runner executor."""
import logging
import time
import threading

from etos_suite_runner.lib.result_handler import ResultHandler
from etos_suite_runner.lib.executor import Executor


class SuiteRunner:  # pylint:disable=too-few-public-methods
    """Test suite runner.

    Splits test suites into sub suites based on number of products available.
    Starts ETOS test runner (ETR) and sends out a test suite finished.
    """

    test_suite_started = None
    result_handler = None
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
        self.result_handler = ResultHandler(self.etos)

        self.context = context

    def _run_etr_and_wait(self, environment):
        """Run ETR based on number of IUTs and wait for them to finish.

        :param environment: Environment which to execute in.
        :type environment: dict
        :return: List of test results from all ETR instances.
        :rtype: list
        """
        self.etos.events.send_announcement_published(
            "[ESR] Starting tests.",
            "Starting test suites on all checked out IUTs.",
            "MINOR",
            {"CONTEXT": self.context},
        )
        self.etos.config.set("nbr_of_suites", len(environment.get("suites", [])))

        executor = Executor(self.etos)
        threads = []
        for suite in environment.get("suites", []):
            thread = threading.Thread(target=executor.run_tests, args=(suite,))
            threads.append(thread)
            thread.start()
            time.sleep(5)
        self.logger.info("Test suites triggered.")
        for thread in threads:
            thread.join()
        self.logger.info("Test suites started.")

        self.etos.events.send_announcement_published(
            "[ESR] Waiting.",
            "Waiting for test suites to finish",
            "MINOR",
            {"CONTEXT": self.context},
        )

        self.logger.info("Wait for test results.")
        self.result_handler.wait_for_test_suite_finished()

    def run(self, environment):
        """Run the suite runner.

        :param environment: Environment in which to run the suite.
        :type environment: dict
        """
        self.logger.info("Started.")

        categories = ["Regression test suite"]
        if self.params.product:
            categories.append(self.params.product)
        test_suite_started = self.etos.events.send_test_suite_started(
            environment.get("suite_name"),
            {"CONTEXT": self.context},
            categories=categories,
            types=["FUNCTIONAL"],
        )
        self.etos.config.set("test_suite_started", test_suite_started.json)

        verdict = "INCONCLUSIVE"
        conclusion = "INCONCLUSIVE"
        description = ""

        try:
            self._run_etr_and_wait(environment)
            verdict, conclusion, description = self.result_handler.test_results()
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
        self.logger.info("Test suite finished.")
