#!/usr/bin/env python
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
# -*- coding: utf-8 -*-
"""ETOS suite runner module."""
import os
import logging
import traceback
import signal
import threading
from uuid import uuid4

from etos_lib import ETOS
from etos_lib.logging.logger import FORMAT_CONFIG

from etos_suite_runner.lib.runner import SuiteRunner
from etos_suite_runner.lib.esr_parameters import ESRParameters
from etos_suite_runner.lib.exceptions import EnvironmentProviderException

# Remove spam from pika.
logging.getLogger("pika").setLevel(logging.WARNING)

LOGGER = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.relpath(__file__))


class ESR:  # pylint:disable=too-many-instance-attributes
    """Suite runner for ETOS main program.

    Run this as a daemon on your system in order to trigger test suites within
    the eiffel event system.
    """

    def __init__(self):
        """Initialize ESR by creating a rabbitmq publisher."""
        self.logger = logging.getLogger("ESR")
        self.etos = ETOS("ETOS Suite Runner", os.getenv("SOURCE_HOST"), "ETOS Suite Runner")
        signal.signal(signal.SIGTERM, self.graceful_exit)
        self.params = ESRParameters(self.etos)
        FORMAT_CONFIG.identifier = self.params.tercc.meta.event_id

        self.etos.config.rabbitmq_publisher_from_environment()
        self.etos.start_publisher()
        self.etos.config.set(
            "WAIT_FOR_ENVIRONMENT_TIMEOUT",
            int(os.getenv("ESR_WAIT_FOR_ENVIRONMENT_TIMEOUT")),
        )

    def _request_environment(self, ids):
        """Request an environment from the environment provider.

        :param ids: Generated suite runner IDs used to correlate environments and the suite
                    runners.
        :type ids: list
        :return: Task ID and an error message.
        :rtype: tuple
        """
        params = {
            "suite_id": self.params.tercc.meta.event_id,
            "suite_runner_ids": ",".join(ids),
        }
        wait_generator = self.etos.http.retry(
            "POST", self.etos.debug.environment_provider, json=params
        )
        task_id = None
        result = {}
        try:
            for response in wait_generator:
                result = response.get("result", "")
                if response and result and result.lower() == "success":
                    task_id = response.get("data", {}).get("id")
                    break
                continue
            else:
                return None, "Did not retrieve an environment"
        except ConnectionError as exception:
            return None, str(exception)
        return task_id, ""

    def _get_environment_status(self, task_id):
        """Wait for an environment being provided.

        :param task_id: Task ID to wait for.
        :type task_id: str
        """
        timeout = self.etos.config.get("WAIT_FOR_ENVIRONMENT_TIMEOUT")
        wait_generator = self.etos.utils.wait(
            self.etos.http.wait_for_request,
            uri=self.etos.debug.environment_provider,
            timeout=timeout,
            params={"id": task_id},
        )
        result = {}
        response = None
        for generator in wait_generator:
            for response in generator:
                result = response.get("result", {}) if response.get("result") is not None else {}
                self.params.set_status(response.get("status"), result.get("error"))
                if response and result:
                    break
            if response and result:
                break
        else:
            self.params.set_status(
                "FAILURE",
                "Unknown Error: Did not receive an environment "
                f"within {self.etos.debug.default_http_timeout}s",
            )

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

    def _reserve_workers(self, ids):
        """Reserve workers for test.

        :param ids: Generated suite runner IDs used to correlate environments and the suite
                    runners.
        :type ids: list
        :return: The environment provider task ID
        :rtype: str
        """
        LOGGER.info("Request environment from environment provider")
        task_id, msg = self._request_environment(ids)
        if task_id is None:
            raise EnvironmentProviderException(msg, task_id)
        return task_id

    def run_suites(self, triggered):
        """Start up a suite runner handling multiple suites that execute within test runners.

        Will only start the test activity if there's a 'slot' available.

        :param triggered: Activity triggered.
        :type triggered: :obj:`eiffel.events.EiffelActivityTriggeredEvent`
        """
        context = triggered.meta.event_id
        self.etos.config.set("context", context)
        LOGGER.info("Sending ESR Docker environment event.")
        self.etos.events.send_environment_defined(
            "ESR Docker", {"CONTEXT": context}, image=os.getenv("SUITE_RUNNER")
        )
        runner = SuiteRunner(self.params, self.etos)

        ids = []
        for suite in self.params.test_suite:
            suite["test_suite_started_id"] = str(uuid4())
            ids.append(suite["test_suite_started_id"])

        task_id = None
        try:
            LOGGER.info("Wait for test environment.")
            task_id = self._reserve_workers(ids)
            self.etos.config.set("task_id", task_id)
            threading.Thread(
                target=self._get_environment_status, args=(task_id,), daemon=True
            ).start()

            self.etos.events.send_activity_started(triggered, {"CONTEXT": context})

            LOGGER.info("Starting ESR.")
            runner.start_suites_and_wait()
        except EnvironmentProviderException as exception:
            task_id = exception.task_id
            LOGGER.info("Release test environment.")
            if task_id is not None:
                self._release_environment(task_id)
            raise

    @staticmethod
    def verify_input():
        """Verify that the data input to ESR are correct."""
        assert os.getenv("SUITE_RUNNER"), "SUITE_RUNNER enviroment variable not provided."
        assert os.getenv("SOURCE_HOST"), "SOURCE_HOST environment variable not provided."
        assert os.getenv("TERCC"), "TERCC environment variable not provided."

    def run(self):
        """Run the ESR main loop."""
        tercc_id = None
        try:
            tercc_id = self.params.tercc.meta.event_id
            self.etos.events.send_announcement_published(
                "[ESR] Launching.",
                "Starting up ESR. Waiting for tests to start.",
                "MINOR",
                {"CAUSE": tercc_id},
            )

            activity_name = "ETOS testrun"
            links = {
                "CAUSE": [
                    self.params.tercc.meta.event_id,
                    self.params.artifact_created["meta"]["id"],
                ]
            }
            triggered = self.etos.events.send_activity_triggered(
                activity_name,
                links,
                executionType="AUTOMATED",
                triggers=[{"type": "EIFFEL_EVENT"}],
            )

            self.verify_input()
            context = triggered.meta.event_id
        except:  # noqa
            self.etos.events.send_announcement_published(
                "[ESR] Failed to start test execution",
                traceback.format_exc(),
                "CRITICAL",
                {"CAUSE": tercc_id},
            )
            raise

        try:
            self.run_suites(triggered)
            self.etos.events.send_activity_finished(
                triggered, {"conclusion": "SUCCESSFUL"}, {"CONTEXT": context}
            )
        except Exception as exception:  # pylint:disable=broad-except
            reason = str(exception)
            self.etos.events.send_activity_canceled(triggered, {"CONTEXT": context}, reason=reason)
            self.etos.events.send_announcement_published(
                "[ESR] Test suite execution failed",
                traceback.format_exc(),
                "MAJOR",
                {"CONTEXT": context},
            )
            raise

    def graceful_exit(self, *_):
        """Attempt to gracefully exit the running job."""
        self.logger.info("Kill command received - Attempting to shut down all processes.")
        raise Exception("Terminate command received - Shutting down.")


def main():
    """Entry point allowing external calls."""
    esr = ESR()
    try:
        esr.run()  # Blocking
    except:
        with open("/dev/termination-log", "w", encoding="utf-8") as termination_log:
            termination_log.write(traceback.format_exc())
        raise
    finally:
        esr.etos.publisher.wait_for_unpublished_events()
        esr.etos.publisher.stop()
    LOGGER.info("ESR Finished Executing.")


def run():
    """Entry point for console_scripts."""
    main()


if __name__ == "__main__":
    run()
