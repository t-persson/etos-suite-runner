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
from typing import Optional, Union
from uuid import uuid4

from eiffellib.events import EiffelTestExecutionRecipeCollectionCreatedEvent
from etos_lib import ETOS
from etos_lib.kubernetes import Environment, Kubernetes, TestRun
from etos_lib.kubernetes.schemas import Environment as EnvironmentSchema
from etos_lib.kubernetes.schemas import Suite
from packageurl import PackageURL

from .exceptions import ArtifactNotFoundException
from .graphql import request_artifact_created


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

    def set_status(self, status: str, error: Optional[str] = None) -> None:
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

    @property
    def etos_controller(self) -> bool:
        """Whether or not the suite runner is running as a part of the ETOS controller."""
        return os.getenv("IDENTIFIER") is not None

    def _get_id(
        self,
        config_key: str,
        environment_variable: str,
        eiffel_event: Union[None, list[dict], dict],
    ) -> str:
        """Get ID will return an ID either from an environment variable or an eiffel event."""
        if self.etos.config.get(config_key) is None:
            if os.getenv(environment_variable) is not None:
                self.etos.config.set(config_key, os.getenv(environment_variable, "Unknown"))
            elif eiffel_event is not None and isinstance(eiffel_event, dict):
                self.etos.config.set(config_key, eiffel_event["meta"]["id"])
        _id = self.etos.config.get(config_key)
        if _id is None:
            raise TypeError(
                f"{config_key} is not set, neither in Eiffel nor {environment_variable} "
                "environment variable"
            )
        return _id

    def environments(self, test_suite_started_id: str) -> list[EnvironmentSchema]:
        """Environments to run tests in."""
        environment_client = Environment(Kubernetes())
        label_selector = (
            f"etos.eiffel-community.github.io/id={self.testrun_id},"
            f"etos.eiffel-community.github.io/suite-id={test_suite_started_id}"
        )
        response = environment_client.client.get(
            namespace=environment_client.namespace,
            label_selector=label_selector,
        )  # type: ignore

        environments = []
        for environment in response.to_dict().get("items", []):
            environments.append(EnvironmentSchema.model_validate(environment))
        return environments

    @property
    def environment_requests(self) -> list:
        """Environment requests for testrun being executed."""
        kubernetes = Kubernetes()
        environment_requests_client = kubernetes.environment_requests
        namespace = kubernetes.namespace
        response = environment_requests_client.get(
            namespace=namespace,
            label_selector=f"etos.eiffel-community.github.io/id={self.testrun_id}",
        )  # type: ignore
        return response.items

    def main_suite_ids(self) -> list[str]:
        """Test suite IDs to set on the main TestSuiteStarted events.

        These IDs are also passed to the environment provider either generated or
        taken from the Environment requests to the environment provider, and are
        used to correlate the environments created with the main test suites.
        """
        if not self.etos_controller:
            return [str(uuid4()) for _ in range(len(self.test_suite))]
        return [request.spec.id for request in self.environment_requests]

    @property
    def testrun_id(self) -> str:
        """Testrun ID returns the ID of a testrun, either from a TERCC or environment."""
        return self._get_id("testrun_id", "IDENTIFIER", self.tercc)

    @property
    def iut_id(self) -> str:
        """Iut ID returns the ID of the artifact that is under test."""
        return self._get_id("iut_id", "ARTIFACT", self.artifact_created)

    @property
    def artifact_created(self) -> Optional[dict]:
        """Artifact under test.

        :return: Artifact created event.
        """
        if self.etos.config.get("artifact_created") is None:
            artifact_id = os.getenv("ARTIFACT")
            if artifact_id is not None:
                artifact_created = request_artifact_created(
                    self.etos,
                    artifact_id=artifact_id,
                )
                if artifact_created is None:
                    raise ArtifactNotFoundException(artifact_id)
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
                if (testrun_id := os.getenv("TESTRUN")) is not None:
                    testrun = TestRun(Kubernetes()).get(testrun_id)
                    self.__test_suite = testrun.spec.suites
                else:
                    tercc = json.loads(os.getenv("TERCC", "{}"))
                    test_suite = self._eiffel_test_suite(tercc)
                    # The dataset is not necessary for the suite runner.
                    test_suite = [Suite.from_tercc(suite, {}) for suite in test_suite]
                    self.__test_suite = test_suite
        return self.__test_suite or []

    def _eiffel_test_suite(self, tercc: dict) -> list[dict]:
        """Eiffel test suite parses an Eiffel TERCC event and returns a list of test suites."""
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
    def product(self) -> Optional[str]:
        """Product name from artifact created event.

        :return: Product name.
        """
        if self.etos.config.get("product") is None:
            identity = None
            if os.getenv("IDENTITY") is not None:
                identity = os.getenv("IDENTITY", "")
            else:
                artifact = self.artifact_created
                if artifact is not None:
                    identity = artifact["data"].get("identity", "")
            if identity is not None:
                purl = PackageURL.from_string(identity)
                self.etos.config.set("product", purl.name)
        return self.etos.config.get("product")
