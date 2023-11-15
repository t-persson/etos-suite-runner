# Copyright Axis Communications AB.
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
import logging
import os
import signal
import threading
import time
import traceback
from json import JSONDecodeError
from uuid import uuid4

from eiffellib.events import EiffelActivityTriggeredEvent
from etos_lib import ETOS
from etos_lib.logging.logger import FORMAT_CONFIG
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError

from .lib.esr_parameters import ESRParameters
from .lib.exceptions import EnvironmentProviderException
from .lib.runner import SuiteRunner

# Remove spam from pika.
logging.getLogger("pika").setLevel(logging.WARNING)


class ESR:  # pylint:disable=too-many-instance-attributes
    """Suite runner for ETOS main program.

    Run this as a daemon on your system in order to trigger test suites within
    the eiffel event system.
    """

    logger = logging.getLogger(__name__)

    def __init__(self) -> None:
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

    def _request_environment(self, ids: list[str]) -> tuple[str, str]:
        """Request an environment from the environment provider.

        :param ids: Generated suite runner IDs used to correlate environments and the suite
                    runners.
        :return: Task ID and an error message.
        """
        params = {
            "suite_id": self.params.tercc.meta.event_id,
            "suite_runner_ids": ",".join(ids),
        }
        try:
            response = self.etos.http.post(self.etos.debug.environment_provider, json=params)
            response.raise_for_status()
        except (RequestsConnectionError, HTTPError) as exception:
            return None, str(exception)

        try:
            json_response = response.json()
        except JSONDecodeError:
            return None, "Could not parse JSON from the environment provider"

        result = json_response.get("result", "")
        if result.lower() != "success":
            return (
                None,
                "Could not retrieve an environment from the environment provider",
            )
        task_id = json_response.get("data", {}).get("id")
        if task_id is None:
            return None, "Did not retrieve an environment"
        return task_id, ""

    def _get_environment_status(self, task_id: str, identifier: str) -> None:
        """Wait for an environment being provided.

        :param task_id: Task ID to wait for.
        :param identifier: An identifier to use for logging.
        """
        FORMAT_CONFIG.identifier = identifier
        timeout = self.etos.config.get("WAIT_FOR_ENVIRONMENT_TIMEOUT")
        end = time.time() + timeout
        while time.time() < end:
            response = self.etos.http.get(
                url=self.etos.debug.environment_provider,
                timeout=timeout,
                params={"id": task_id},
            )
            response.raise_for_status()
            json_response = response.json()
            # dict.get() does not work here as it only sets None if the key does not exist
            # and sometimes the key exists, but the value is None.
            result = json_response.get("result") or {}
            self.params.set_status(json_response.get("status"), result.get("error"))
            if json_response and result:
                break
            time.sleep(5)
        else:
            self.params.set_status(
                "FAILURE",
                "Unknown Error: Did not receive an environment " f"within {timeout}s",
            )
        if self.params.get_status().get("error") is not None:
            self.logger.error(
                "Environment provider has failed in creating an environment for test.",
                extra={"user_log": True},
            )
        else:
            self.logger.info(
                "Environment provider has finished creating an environment for test.",
                extra={"user_log": True},
            )

    def _release_environment(self, task_id: str) -> None:
        """Release an environment from the environment provider.

        :param task_id: Task ID to release.
        """
        response = self.etos.http.get(
            self.etos.debug.environment_provider, params={"release": task_id}
        )
        response.raise_for_status()

    def _reserve_workers(self, ids: list[str]) -> str:
        """Reserve workers for test.

        :param ids: Generated suite runner IDs used to correlate environments and the suite
                    runners.
        :return: The environment provider task ID
        """
        self.logger.info("Request environment from environment provider", extra={"user_log": True})
        task_id, msg = self._request_environment(ids)
        if task_id is None:
            raise EnvironmentProviderException(msg, task_id)
        return task_id

    def run_suites(self, triggered: EiffelActivityTriggeredEvent, tercc_id: str) -> None:
        """Start up a suite runner handling multiple suites that execute within test runners.

        Will only start the test activity if there's a 'slot' available.

        :param triggered: Activity triggered.
        :param tercc_id: The ID of the tercc that is going to be executed.
        """
        context = triggered.meta.event_id
        self.etos.config.set("context", context)
        self.logger.info("Sending ESR Docker environment event.")
        self.etos.events.send_environment_defined(
            "ESR Docker", {"CONTEXT": context}, image=os.getenv("SUITE_RUNNER")
        )
        runner = SuiteRunner(self.params, self.etos)

        ids = []
        for suite in self.params.test_suite:
            suite["test_suite_started_id"] = str(uuid4())
            ids.append(suite["test_suite_started_id"])
        self.logger.info("Number of test suites to run: %d", len(ids), extra={"user_log": True})

        task_id = None
        try:
            self.logger.info("Wait for test environment.")
            task_id = self._reserve_workers(ids)
            self.etos.config.set("task_id", task_id)
            threading.Thread(
                target=self._get_environment_status,
                args=(task_id, tercc_id),
                daemon=True,
            ).start()

            self.etos.events.send_activity_started(triggered, {"CONTEXT": context})

            self.logger.info("Starting ESR.")
            runner.start_suites_and_wait()
        except EnvironmentProviderException as exception:
            task_id = exception.task_id
            self.logger.info("Release test environment.")
            if task_id is not None:
                self._release_environment(task_id)
            raise

    @staticmethod
    def verify_input() -> None:
        """Verify that the data input to ESR are correct."""
        assert os.getenv("SUITE_RUNNER"), "SUITE_RUNNER enviroment variable not provided."
        assert os.getenv("SOURCE_HOST"), "SOURCE_HOST environment variable not provided."
        assert os.getenv("TERCC"), "TERCC environment variable not provided."

    def run(self) -> None:
        """Run the ESR main loop."""
        tercc_id = None
        try:
            tercc_id = self.params.tercc.meta.event_id
            self.logger.info("ETOS suite runner is starting up", extra={"user_log": True})
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
            self.logger.exception(
                "ETOS suite runner failed to start test execution",
                extra={"user_log": True},
            )
            self.etos.events.send_announcement_published(
                "[ESR] Failed to start test execution",
                traceback.format_exc(),
                "CRITICAL",
                {"CAUSE": tercc_id},
            )
            raise

        try:
            self.run_suites(triggered, tercc_id)
            self.etos.events.send_activity_finished(
                triggered, {"conclusion": "SUCCESSFUL"}, {"CONTEXT": context}
            )
        except Exception as exception:  # pylint:disable=broad-except
            reason = str(exception)
            self.logger.exception(
                "ETOS suite runner failed to execute test suite",
                extra={"user_log": True},
            )
            self.etos.events.send_activity_canceled(triggered, {"CONTEXT": context}, reason=reason)
            self.etos.events.send_announcement_published(
                "[ESR] Test suite execution failed",
                traceback.format_exc(),
                "MAJOR",
                {"CONTEXT": context},
            )
            raise

    def graceful_exit(self, *_) -> None:
        """Attempt to gracefully exit the running job."""
        self.logger.info("Kill command received - Attempting to shut down all processes.")
        raise RuntimeError("Terminate command received - Shutting down.")
