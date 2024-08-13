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
import time
import threading
from uuid import uuid4

from eiffellib.events import (
    EiffelActivityTriggeredEvent, EiffelTestExecutionRecipeCollectionCreatedEvent,
)
from environment_provider.environment_provider import EnvironmentProvider
from environment_provider.environment import release_full_environment
from etos_lib import ETOS
from etos_lib.logging.logger import FORMAT_CONFIG
from etos_lib.kubernetes.schemas.testrun import Suite
from jsontas.jsontas import JsonTas
import opentelemetry
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .lib.esr_parameters import ESRParameters
from .lib.exceptions import EnvironmentProviderException
from .lib.graphql import request_tercc
from .lib.runner import SuiteRunner
from .lib.otel_tracing import get_current_context, OpenTelemetryBase

# Remove spam from pika.
logging.getLogger("pika").setLevel(logging.WARNING)


class ESR(OpenTelemetryBase):  # pylint:disable=too-many-instance-attributes
    """Suite runner for ETOS main program.

    Run this as a daemon on your system in order to trigger test suites within
    the eiffel event system.
    """

    logger = logging.getLogger(__name__)

    def __init__(self) -> None:
        """Initialize ESR by creating a rabbitmq publisher."""
        self.logger = logging.getLogger("ESR")
        self.otel_tracer = opentelemetry.trace.get_tracer(__name__)
        self.otel_context = get_current_context()
        self.otel_context_token = opentelemetry.context.attach(self.otel_context)
        self.etos = ETOS("ETOS Suite Runner", os.getenv("SOURCE_HOST"), "ETOS Suite Runner")
        signal.signal(signal.SIGTERM, self.graceful_exit)
        self.params = ESRParameters(self.etos)
        FORMAT_CONFIG.identifier = self.params.testrun_id

        self.etos.config.rabbitmq_publisher_from_environment()
        self.etos.start_publisher()
        self.etos.config.set(
            "WAIT_FOR_ENVIRONMENT_TIMEOUT",
            int(os.getenv("ESR_WAIT_FOR_ENVIRONMENT_TIMEOUT")),
        )

    def __del__(self):
        """Destructor."""
        if self.otel_context_token is not None:
            opentelemetry.context.detach(self.otel_context_token)

    def __environment_request_status(self) -> None:
        """Continuosly check environment request status."""
        timeout = time.time() + self.etos.config.get("WAIT_FOR_ENVIRONMENT_TIMEOUT")
        span_name = "environment_request"
        with self.otel_tracer.start_as_current_span(
            span_name,
            kind=opentelemetry.trace.SpanKind.CLIENT,
        ):
            while time.time() <= timeout:
                time.sleep(5)
                failed = []
                success = []
                requests = []
                found = False
                for request in self.params.environment_requests:
                    requests.append(request)
                    # This condition check is temporary to make sure that the ESR fails if environment
                    # requests fail. In the future the ESR shall not even start if the environment request
                    # does not finish.
                    for condition in request.status.conditions:
                        _type = condition.get("type", "").lower()
                        if _type == "ready":
                            found = True
                            status = condition.get("status", "").lower()
                            reason = condition.get("reason", "").lower()
                            if status == "false" and reason == "failed":
                                failed.append(condition)
                            if status == "false" and reason == "done":
                                success.append(condition)
                if found and len(failed) > 0:
                    for request in failed:
                        self.logger.error(request.get("message"))
                    self.params.set_status("FAILURE", failed[-1].get("message"))
                    self.logger.error(
                        "Environment provider has failed in creating an environment for test.",
                        extra={"user_log": True},
                    )
                    break
                if found and len(success) == len(requests):
                    self.params.set_status("SUCCESS", "Successfully created an environment for test")
                    self.logger.info(
                        "Environment provider has finished creating an environment for test.",
                        extra={"user_log": True},
                    )
                    break

    def __request_environment(self, ids: list[str]) -> None:
        """Request an environment from the environment provider.

        :param ids: Generated suite runner IDs used to correlate environments and the suite
                    runners.
        """
        span_name = "request_environment"
        with self.otel_tracer.start_as_current_span(
            span_name,
            kind=opentelemetry.trace.SpanKind.CLIENT,
        ):
            try:
                provider = EnvironmentProvider(ids)
                result = provider.run()
            except Exception as exc:
                self.params.set_status("FAILURE", "Failed to run environment provider")
                self.logger.error(
                    "Environment provider has failed in creating an environment for test.",
                    extra={"user_log": True},
                )
                self._record_exception(exc)
                raise
            if result.get("error") is not None:
                self.params.set_status("FAILURE", result.get("error"))
                self.logger.error(
                    "Environment provider has failed in creating an environment for test.",
                    extra={"user_log": True},
                )
                exc = Exception(str(result.get("error")))
                self._record_exception(exc)
            else:
                self.params.set_status("SUCCESS", result.get("error"))
                self.logger.info(
                    "Environment provider has finished creating an environment for test.",
                    extra={"user_log": True},
                )

    def _request_environment(self, ids: list[str], otel_context_carrier: dict) -> None:
        """Request an environment from the environment provider (OpenTelemetry wrapper).

        :param ids: Generated suite runner IDs used to correlate environments and the suite
                    runners.
        :param otel_context_carrier: a dict carrying current OpenTelemetry context.
        """
        # OpenTelemetry contexts aren't propagated to threads automatically.
        # For this reason otel_context needs to be reinstantiated due to
        # this method running in a separate thread.
        otel_context = TraceContextTextMapPropagator().extract(carrier=otel_context_carrier)
        otel_context_token = opentelemetry.context.attach(otel_context)
        try:
            if os.getenv("IDENTIFIER") is not None:
                self.__environment_request_status()
            else:
                self.__request_environment(ids)
        finally:
            opentelemetry.context.detach(otel_context_token)

    def _release_environment(self) -> None:
        """Release an environment from the environment provider."""
        # TODO: We should remove jsontas as a requirement for this function.
        # Passing variables as keyword argument to make it easier to transition to a function where
        # jsontas is not required.
        jsontas = JsonTas()
        span_name = "release_full_environment"
        with self.otel_tracer.start_as_current_span(
            span_name,
            context=self.otel_context,
            kind=opentelemetry.trace.SpanKind.CLIENT,
        ):
            status, message = release_full_environment(
                etos=self.etos,
                jsontas=jsontas,
                suite_id=self.params.testrun_id,
            )
            if not status:
                self.logger.error(message)

    def run_suites(self, triggered: EiffelActivityTriggeredEvent) -> list[str]:
        """Start up a suite runner handling multiple suites that execute within test runners.

        Will only start the test activity if there's a 'slot' available.

        :param triggered: Activity triggered.
        :return: List of main suite IDs - Used for tests
        """
        context = triggered.meta.event_id
        self.etos.config.set("context", context)
        self.logger.info("Sending ESR Docker environment event.")
        runner = SuiteRunner(self.params, self.etos)
        suites: list[tuple[str, Suite]] = []
        ids = self.params.main_suite_ids()
        for i, suite in enumerate(self.params.test_suite):
            suites.append((ids[i], suite))
        self.logger.info("Number of test suites to run: %d", len(suites), extra={"user_log": True})
        try:
            self.logger.info("Get test environment.")
            carrier = {}
            TraceContextTextMapPropagator().inject(carrier)
            threading.Thread(
                target=self._request_environment,
                args=(
                    [id for id, _ in suites.copy()],
                    carrier,
                ),
                daemon=True,
            ).start()

            self.etos.events.send_activity_started(triggered, {"CONTEXT": context})

            self.logger.info("Starting ESR.")
            runner.start_suites_and_wait(suites)
            return [id for id, _ in suites]
        except EnvironmentProviderException as exc:
            self.logger.info("Release test environment.")
            self._release_environment()
            self._record_exception(exc)
            raise exc

    @staticmethod
    def verify_input() -> None:
        """Verify that the data input to ESR are correct."""
        assert os.getenv("SOURCE_HOST"), "SOURCE_HOST environment variable not provided."
        assert os.getenv("TERCC"), "TERCC environment variable not provided."

    def _send_tercc(self, testrun_id: str, iut_id: str) -> None:
        """Send tercc will publish the TERCC event for this testrun."""
        self.logger.info("Sending TERCC event")
        event = EiffelTestExecutionRecipeCollectionCreatedEvent()
        event.meta.event_id = testrun_id
        links = {"CAUSE": iut_id}
        data = {
            "selectionStrategy": {"tracker": "Suite Builder", "id": str(uuid4())},
            "batchesUri": os.getenv("SUITE_SOURCE", "Unknown"),
        }
        self.etos.events.send(event, links, data)

    def run(self) -> list[str]:
        """Run the ESR main loop.

        :return: List of test suites (main suites) that were started.
        """
        testrun_id = None
        try:
            testrun_id = self.params.testrun_id
            self.logger.info("ETOS suite runner is starting up", extra={"user_log": True})
            if os.getenv("IDENTIFIER") is not None:
                # We are probably running as a TestRun
                if request_tercc(self.etos, testrun_id) is None:
                    self._send_tercc(testrun_id, self.params.iut_id)
 
            activity_name = "ETOS testrun"
            links = {
                "CAUSE": [
                    testrun_id,
                    self.params.iut_id,
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
            raise

        try:
            ids = self.run_suites(triggered)
            self.etos.events.send_activity_finished(
                triggered, {"conclusion": "SUCCESSFUL"}, {"CONTEXT": context}
            )
            return ids
        except Exception as exception:  # pylint:disable=broad-except
            reason = str(exception)
            self.logger.exception(
                "ETOS suite runner failed to execute test suite",
                extra={"user_log": True},
            )
            self.etos.events.send_activity_canceled(triggered, {"CONTEXT": context}, reason=reason)
            self._record_exception(exception)
            raise

    def graceful_exit(self, *_) -> None:
        """Attempt to gracefully exit the running job."""
        self.logger.info("Kill command received - Attempting to shut down all processes.")
        raise RuntimeError("Terminate command received - Shutting down.")
