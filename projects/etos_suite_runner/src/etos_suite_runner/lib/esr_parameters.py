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
"""ESR parameters module."""
import json
import logging
import os
from threading import Lock
from typing import Union, Callable

from etos_lib import ETOS
from eiffellib.events import EiffelTestExecutionRecipeCollectionCreatedEvent
from packageurl import PackageURL

from .graphql import request_artifact_created
from .testrun import Suite


class ESRParameters:
    """Parameters required for ESR."""

    logger = logging.getLogger("ESRParameters")
    lock = Lock()
    __test_suite = None

    def __init__(self, etos: ETOS) -> None:
        """ESR parameters instance."""
        self.etos = etos
        self.issuer = {"name": "ETOS Suite Runner"}
        self.environment_status = {"status": "NOT_STARTED", "error": None}

    def set_status(self, status: str, error: str) -> None:
        """Set environment provider status."""
        with self.lock:
            self.logger.debug("Setting environment status to %r, error %r", status, error)
            self.environment_status["status"] = status
            self.environment_status["error"] = error

    def get_status(self) -> dict:
        """Get environment provider status.

        :return: Status dictionary for the environment provider.
        """
        with self.lock:
            return self.environment_status.copy()

    def _get_id(
        self, config_key: str, environment_variable: str, eiffel_event: Union[list[dict], dict]
    ) -> str:
        """Get ID will return an ID either from an environment variable or an eiffel event."""
        if self.etos.config.get(config_key) is None:
            if os.getenv(environment_variable) is not None:
                self.etos.config.set(config_key, os.getenv(environment_variable, "Unknown"))
            else:
                self.etos.config.set(config_key, eiffel_event["meta"]["id"])
        _id = self.etos.config.get(config_key)
        if _id is None:
            raise TypeError(
                f"{config_key} is not set, neither in Eiffel nor {environment_variable} "
                "environment variable"
            )
        return _id

    @property
    def testrun_id(self) -> str:
        """Testrun ID returns the ID of a testrun, either from a TERCC or environment."""
        return self._get_id("testrun_id", "IDENTIFIER", self.tercc)

    @property
    def iut_id(self) -> str:
        """Iut ID returns the ID of the artifact that is under test."""
        return self._get_id("iut_id", "ARTIFACT", self.artifact_created)

    @property
    def artifact_created(self) -> dict:
        """Artifact under test.

        :return: Artifact created event.
        """
        if self.etos.config.get("artifact_created") is None:
            if os.getenv("ARTIFACT") is not None:
                artifact_created = request_artifact_created(self.etos, artifact_id=os.getenv("ARTIFACT"))
            else:
                tercc = EiffelTestExecutionRecipeCollectionCreatedEvent()
                tercc.rebuild(self.tercc)
                artifact_created = request_artifact_created(self.etos, tercc=tercc)
            self.etos.config.set("artifact_created", artifact_created)
        return self.etos.config.get("artifact_created")

    @property
    def tercc(self) -> Union[list[dict], dict]:
        """Test execution recipe collection created event from environment.

        :return: Test execution event.
        """
        if self.etos.config.get("tercc") is None:
            tercc = json.loads(os.getenv("TERCC", "{}"))
            self.etos.config.set("tercc", tercc)
        return self.etos.config.get("tercc")

    @property
    def test_suite(self) -> list[Suite]:
        """Download and return test batches."""
        with self.lock:
            if self.__test_suite is None:
                tercc = json.loads(os.getenv("TERCC", "{}"))
                if isinstance(tercc, list):
                    self.__test_suite = [Suite(**suite) for suite in tercc]
                else:
                    test_suite = self._eiffel_test_suite(tercc)
                    self.__test_suite = [Suite.from_tercc(suite) for suite in test_suite]
        return self.__test_suite or []

    def _eiffel_test_suite(self, tercc: dict) -> list[dict]:
        """Eiffel test suite parses an Eiffel TERCC even and returns a list of test suites."""
        batch = tercc.get("data", {}).get("batches")
        batch_uri = tercc.get("data", {}).get("batchesUri")
        if batch is not None and batch_uri is not None:
            raise ValueError("Only one of 'batches' or 'batchesUri' shall be set")
        if batch is not None:
            return batch
        if batch_uri is not None:
            json_header = {"Accept": "application/json"}
            response = self.etos.http.get(
                batch_uri,
                headers=json_header,
            )
            response.raise_for_status()
            return response.json()
        raise ValueError("At least one of 'batches' or 'batchesUri' shall be set")

    @property
    def product(self) -> str:
        """Product name from artifact created event.

        :return: Product name.
        """
        if self.etos.config.get("product") is None:
            if os.getenv("IDENTITY") is not None:
                identity = os.getenv("IDENTITY", "")
            else:
                identity = self.artifact_created["data"].get("identity", "")
            purl = PackageURL.from_string(identity)
            self.etos.config.set("product", purl.name)
        return self.etos.config.get("product")
