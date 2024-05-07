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
"""Executor handler module."""
import logging
import os
from json import JSONDecodeError, dumps
from typing import Union

from cryptography.fernet import Fernet
from etos_lib import ETOS
from etos_lib.opentelemetry.semconv import Attributes as SemConvAttributes
from opentelemetry import trace
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import HTTPError

from .otel_tracing import OpenTelemetryBase


class TestStartException(Exception):
    """Exception when starting tests."""

    def __init__(self, message: dict):
        """Initialize with a dict instead of str."""
        super().__init__(str(message))
        self.error = message.get("error", "Unknown error when starting tests")


class Executor(OpenTelemetryBase):  # pylint:disable=too-few-public-methods
    """Executor for launching ETR."""

    logger = logging.getLogger("ESR - Executor")

    def __init__(self, etos: ETOS) -> None:
        """Initialize executor.

        :param etos: ETOS library instance.
        """
        self.etos = etos
        self.etos.config.set("build_urls", [])
        self.tracer = trace.get_tracer(__name__)

    def __decrypt(self, password: Union[str, dict]) -> str:
        """Decrypt a password using an encryption key.

        :param password: Password to decrypt.
        :return: Decrypted password
        """
        key = os.getenv("ETOS_ENCRYPTION_KEY")
        if key is None:
            self.logger.debug("No encryption key available, won't decrypt password")
            return password
        password_value = password.get("$decrypt", {}).get("value")
        if password_value is None:
            self.logger.debug("No '$decrypt' JSONTas struct for password, won't decrypt password")
            return password
        return Fernet(key).decrypt(password_value).decode()

    def __auth(
        self, username: str, password: str, type: str = "basic"  # pylint:disable=redefined-builtin
    ) -> Union[HTTPBasicAuth, HTTPDigestAuth]:
        """Create an authentication for HTTP request.

        :param username: Username to authenticate.
        :param password: Password to authenticate with.
        :param type: Type of authentication. 'basic' or 'digest'.
        :return: Authentication method.
        """
        password = self.__decrypt(password)
        if type.lower() == "basic":
            return HTTPBasicAuth(username, password)
        return HTTPDigestAuth(username, password)

    def run_tests(self, test_suite: dict) -> None:
        """Run tests in jenkins.

        :param test_suite: Tests to execute.
        """
        executor = test_suite.get("executor")
        request = executor.get("request")
        if request.get("auth"):
            request["auth"] = self.__auth(**request["auth"])
        method = getattr(self.etos.http, request.pop("method").lower())
        span_name = "start_execution_space"
        with self.tracer.start_as_current_span(span_name, kind=trace.SpanKind.CLIENT) as span:
            span.set_attribute(
                SemConvAttributes.EXECUTOR_ID, executor["id"] if "id" in executor else ""
            )
            span.set_attribute("http.request.body", dumps(request))
            try:
                response = method(**request)
                response.raise_for_status()
            except HTTPError as http_error:
                try:
                    exc = TestStartException(http_error.response.json())
                    self._record_exception(exc)
                    raise exc from http_error
                except JSONDecodeError:
                    exc = TestStartException({"error": http_error.response.text})
                    self._record_exception(exc)
                    raise exc from http_error
            except RequestsConnectionError as connection_error:
                exc = TestStartException({"error": str(connection_error)})
                self._record_exception(exc)
                raise exc from connection_error

            self.logger.info("%r", response)
            self.logger.debug("%r", response.text)
