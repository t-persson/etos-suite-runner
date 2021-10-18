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


class SuiteRunner:
    """Test suite runner.

    Feature flag:
        If feature flag CLM is set to false, then this class
        will not send confidence levels.

    Splits test suites into sub suites based on number of products available.
    Starts ETOS test runner (ETR) and sends out a confidence level.
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

    def confidence_level(self, test_suite_started, environment):
        """Publish a confidence level modified based on sub confidences.

        :param test_suite_started: Test suite to set as CAUSE for this confidence.
        :type test_suite_started: :obj:`eiffel.events.EiffelTestSuiteStartedEvent`
        :param environment: Environment in which the test suite was run.
        :type environment: dict
        """
        self.logger.warning("DEPRECATED: Please note that confidence levels are deprecated in ETOS.\n"
                            "Set feature flag CLM to false in order to disable this deprecated feature.")
        links = {
            "CONTEXT": self.context,
            "CAUSE": test_suite_started,
            "SUBJECT": self.params.artifact_created["meta"]["id"],
        }

        failures, inconclusives = 0, 0
        for sub_confidence in self.result_handler.confidence_levels:
            links.setdefault("SUB_CONFIDENCE_LEVEL", [])
            links["SUB_CONFIDENCE_LEVEL"].append(sub_confidence["meta"]["id"])

            if sub_confidence["data"]["value"] == "FAILURE":
                failures += 1
            elif sub_confidence["data"]["value"] == "INCONCLUSIVE":
                inconclusives += 1

        if failures == 0 and inconclusives == 0:
            value = "SUCCESS"
        elif failures >= inconclusives:
            value = "FAILURE"
        else:
            value = "INCONCLUSIVE"

        self.etos.events.send_confidence_level_modified(
            environment.get("suite_name"), value, links, issuer=self.params.issuer
        )

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
            if self.etos.feature_flags.clm:
                self.confidence_level(test_suite_started, environment)
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
