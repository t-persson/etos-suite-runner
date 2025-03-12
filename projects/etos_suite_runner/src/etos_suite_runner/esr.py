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
import re
import signal
import time
import threading
import traceback
from uuid import uuid4

from eiffellib.events import (
    EiffelActivityTriggeredEvent,
    EiffelTestExecutionRecipeCollectionCreatedEvent,
)
from environment_provider.environment_provider import EnvironmentProvider
from environment_provider.environment import release_full_environment
from etos_lib import ETOS
from etos_lib.logging.logger import FORMAT_CONFIG
from etos_lib.kubernetes.schemas.testrun import Suite
from jsontas.jsontas import JsonTas
import opentelemetry
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from etos_suite_runner.lib.result import Result

from .lib.esr_parameters import ESRParameters
from .lib.exceptions import EnvironmentProviderException
from .lib.graphql import request_tercc
from .lib.runner import SuiteRunner
from .lib.otel_tracing import get_current_context, OpenTelemetryBase
from .lib.events import EventPublisher

# Remove spam from pika.
logging.getLogger("pika").setLevel(logging.WARNING)


class ESR(OpenTelemetryBase):  # pylint:disable=too-many-instance-attributes
    """Suite runner for ETOS main program.

    Run this as a daemon on your system in order to trigger test suites within
    the eiffel event system.
    """

    logger = logging.getLogger(__name__)

    def __init__(self, etos: ETOS) -> None:
        """Initialize ESR by creating a rabbitmq publisher."""
        self.logger = logging.getLogger("ESR")
        self.otel_tracer = opentelemetry.trace.get_tracer(__name__)
        self.otel_context = get_current_context()
        self.otel_context_token = opentelemetry.context.attach(self.otel_context)
        self.etos = etos
        signal.signal(signal.SIGTERM, self.graceful_exit)
        self.params = ESRParameters(self.etos)
        FORMAT_CONFIG.identifier = self.params.testrun_id

        self.etos.config.rabbitmq_publisher_from_environment()
        self.etos.start_publisher()
        self.etos.config.set(
            "WAIT_FOR_ENVIRONMENT_TIMEOUT",
            int(os.getenv("ESR_WAIT_FOR_ENVIRONMENT_TIMEOUT")),
        )
        self.event_publisher = EventPublisher(etos, self.params.testrun_id)

    def __del__(self):
        """Destructor."""
        if self.otel_context_token is not None:
            opentelemetry.context.detach(self.otel_context_token)

    def __environment_request_status(self) -> None:
        """Continuously check environment request status."""
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
                    # This condition check is temporary to make sure that the ESR fails if
                    # environment requests fail. In the future the ESR shall not even start
                    # if the environment request does not finish.
                    for condition in request.status.conditions:
                        _type = condition.get("type", "").lower()
                        if _type == "ready":
                            found = True
                            status = condition.get("status", "").lower()
                            reason = condition.get("reason", "").lower()
                            if status == "false" and reason == "failed":
                                failed.append(condition)
                            if status == "true" and reason == "done":
                                success.append(condition)
                if found and len(failed) > 0:
                    for condition in failed:
                        self.logger.error(condition.get("message"))
                    self.params.set_status("FAILURE", failed[-1].get("message"))
                    self.logger.error(
                        "Environment provider has failed in creating an environment for test.",
                        extra={"user_log": True},
                    )
                    break
                if found and len(success) == len(requests):
                    self.params.set_status("SUCCESS", None)
                    self.logger.info(
                        "Environment provider has finished creating an environment for test.",
                        extra={"user_log": True},
                    )
                    break
        self.logger.info("Environmentrequest finished")

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
                self.logger.exception(
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
                exc = self._parse_environment_exception(
                    result.get("details", ""), result.get("error", "")
                )
                self._record_exception(exc)
            else:
                self.params.set_status("SUCCESS", result.get("error"))
                self.logger.info(
                    "Environment provider has finished creating an environment for test.",
                    extra={"user_log": True},
                )

    def _parse_environment_exception(self, traceback_str: str, fallback_msg: str) -> Exception:
        """Parse a traceback string extracting the type and return the corresponding exception.

        This function uses a simple regular expression to parse the
        traceback string. It may not work correctly for all possible
        input formats.
        """
        try:
            # format_exc always ends in \n\n so get the traceback string from the correct line.
            traceback_str = traceback_str.split("\n")[-2]
        except IndexError:
            self.logger.warning("Failed to parse exception")
            return Exception(fallback_msg)
        match = re.search(r"([a-zA-Z_][\w\.]+)(?:\: (.+))?", traceback_str)
        if match:
            exc_type = match.group(1)
            exc_msg = match.group(2) or ""
            try:
                exc_class = globals()[exc_type]
                return exc_class(exc_msg)
            except KeyError:
                self.logger.warning("Unknown exception type: %s", exc_type)
                return Exception(fallback_msg)
        else:
            self.logger.warning("Failed to parse exception")
            return Exception(fallback_msg)

    def _request_environment(self, ids: list[str], otel_context_carrier: dict) -> None:
        """Request an environment from the environment provider (OpenTelemetry wrapper).

        :param ids: Generated suite runner IDs used to correlate environments and the suite
                    runners.
        :param otel_context_carrier: a dict carrying current OpenTelemetry context.
        """
        FORMAT_CONFIG.identifier = self.params.testrun_id
        # OpenTelemetry contexts aren't propagated to threads automatically.
        # For this reason otel_context needs to be reinstantiated due to
        # this method running in a separate thread.
        otel_context = TraceContextTextMapPropagator().extract(carrier=otel_context_carrier)
        otel_context_token = opentelemetry.context.attach(otel_context)
        try:
            # TestRun identifier, correlates to the TERCC that should be sent when
            # running in the ETOS Kubernetes controller environment.
            if self.params.etos_controller:
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
        span_name = "esr_release_full_environment"
        with self.otel_tracer.start_as_current_span(
            span_name,
            context=self.otel_context,
            kind=opentelemetry.trace.SpanKind.CLIENT,
        ) as span:
            span.set_attribute("esr.params.testrun_id", self.params.testrun_id)
            status, message = release_full_environment(
                etos=self.etos,
                jsontas=jsontas,
                suite_id=self.params.testrun_id,
            )
            if not status:
                self.logger.error(message)

    def run_suites(self, triggered: EiffelActivityTriggeredEvent):
        """Start up a suite runner handling multiple suites that execute within test runners.

        Will only start the test activity if there's a 'slot' available.

        :param triggered: Activity triggered.
        """
        context = triggered.meta.event_id
        self.etos.config.set("context", context)
        self.logger.info("Sending ESR Docker environment event.")
        runner = SuiteRunner(self.params, self.etos)
        suites: list[tuple[str, Suite]] = []
        ids = self.params.main_suite_ids()
        for i, suite in enumerate(self.params.test_suite):
            suites.append((ids[i], suite))
        self.etos.config.set("ids", ids)  # Used for testing
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
        except EnvironmentProviderException as exc:
            # Not running as part of the ETOS Kubernetes controller environment
            if not self.params.etos_controller:
                self.logger.info("Release test environment.")
                self._release_environment()
            self._record_exception(exc)
            raise exc

    @staticmethod
    def verify_input() -> None:
        """Verify that the data input to ESR are correct."""
        assert os.getenv("SOURCE_HOST"), "SOURCE_HOST environment variable not provided."

        if os.getenv("TESTRUN") is None:
            # TERCC variable is set only in v0 ETOS, TESTRUN in v1 and onwards.
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

    def _run(self):
        """Run the ESR main loop."""
        testrun_id = None
        try:
            testrun_id = self.params.testrun_id
            self.logger.info("ETOS suite runner is starting up", extra={"user_log": True})
            # TestRun identifier, correlates to the TERCC that should be sent when
            # running in the ETOS Kubernetes controller environment.
            if self.params.etos_controller:
                # We are probably running in the ETOS Kubernetes controller environment
                self.logger.info("Checking TERCC")
                if request_tercc(self.etos, testrun_id) is None:
                    self.logger.info("Sending TERCC")
                    self._send_tercc(testrun_id, self.params.iut_id)

            activity_name = "ETOS testrun"
            links = {
                "CAUSE": [
                    testrun_id,
                    self.params.iut_id,
                ]
            }
            self.logger.info("Sending activity triggered for the ETOS testrun")
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
            self.run_suites(triggered)
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
            self._record_exception(exception)
            raise

    def result(self) -> Result:
        """ESR execution result."""
        results = self.etos.config.get("results") or []
        result = {}
        for suite_result in results:
            if suite_result.get("verdict") == "FAILED":
                result = suite_result
                # If the verdict on any main suite is FAILED, that is the verdict we set on the
                # test run, which means that we can break the loop early in that case.
                break
            if suite_result.get("verdict") == "INCONCLUSIVE":
                result = suite_result
        if len(results) == 0:
            result = {
                "conclusion": "Inconclusive",
                "verdict": "Inconclusive",
                "description": "Got no results from ESR",
            }
        elif result == {}:
            # No suite failed, so lets just pick the first result
            result = results[0]
        return Result(**result)

    def run(self) -> Result:
        """Run the ESR main loop.

        :return: List of test suites (main suites) that were started.
        """
        self.etos.config.set("results", [])
        result = Result(
            verdict="Inconclusive",
            conclusion="Failed",
            description="ESR did not execute",
        )
        try:
            self._run()
            result = self.result()
            return result
        except Exception:  # pylint:disable=bare-except
            result = Result(
                conclusion="Failed",
                verdict="Inconclusive",
                description=traceback.format_exc(),
            )
            raise
        finally:
            event = {
                "event": "shutdown",
                "data": result.model_dump(),
            }
            self.event_publisher.publish(event)

    def graceful_exit(self, *_) -> None:
        """Attempt to gracefully exit the running job."""
        self.logger.info("Kill command received - Attempting to shut down all processes.")
        raise RuntimeError("Terminate command received - Shutting down.")
