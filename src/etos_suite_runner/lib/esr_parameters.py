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
import os
import json
import time
import logging
from threading import Lock

from packageurl import PackageURL
from etos_lib.logging.logger import FORMAT_CONFIG
from eiffellib.events import EiffelTestExecutionRecipeCollectionCreatedEvent
from .graphql import request_environment_defined
from .exceptions import EnvironmentProviderException

ARTIFACTS = """
{
  artifactCreated(search: "{'meta.id': '%s'}") {
    edges {
      node {
        __typename
        data {
          identity
        }
        meta {
          id
        }
      }
    }
  }
}
"""


class ESRParameters:
    """Parameters required for ESR."""

    logger = logging.getLogger("ESRParameters")
    lock = Lock()
    environment_provider_done = False
    error = False
    __test_suite = None

    def __init__(self, etos):
        """ESR parameters instance."""
        self.etos = etos
        self.issuer = {"name": "ETOS Suite Runner"}
        self.environment_status = {"status": "NOT_STARTED", "error": None}
        self.__environments = {}

    def set_status(self, status, error):
        """Set environment provider status."""
        with self.lock:
            self.logger.debug("Setting environment status to %r, error %r", status, error)
            self.environment_status["status"] = status
            self.environment_status["error"] = error

    def get_node(self, response):
        """Get a single node from a GraphQL response.

        :param response: GraphQL response dictionary.
        :type response: dict
        :return: Node dictionary or None.
        :rtype: dict
        """
        try:
            return next(self.etos.utils.search(response, "node"))[1]
        except StopIteration:
            return None

    def __get_artifact_created(self):
        """Fetch artifact created events from GraphQL.

        :return: Artifact created event.
        :rtype: :obj:`EiffelArtifactCreatedEvent`
        """
        wait_generator = self.etos.utils.wait(
            self.etos.graphql.execute,
            query=ARTIFACTS % self.etos.utils.eiffel_link(self.tercc, "CAUSE"),
        )

        for response in wait_generator:
            created_node = self.get_node(response)
            if not created_node:
                continue
            self.etos.config.set("artifact_created", created_node)
            return created_node
        return None

    @property
    def artifact_created(self):
        """Artifact under test.

        :return: Artifact created event.
        :rtype: :obj:`EiffelArtifactCreatedEvent`
        """
        if self.etos.config.get("artifact_created") is None:
            self.__get_artifact_created()
        return self.etos.config.get("artifact_created")

    @property
    def tercc(self):
        """Test execution recipe collection created event from environment.

        :return: Test execution event.
        :rtype: :obj:`EiffelTestExecutionRecipeCollectionCreatedEvent`
        """
        if self.etos.config.get("tercc") is None:
            tercc = EiffelTestExecutionRecipeCollectionCreatedEvent()
            tercc.rebuild(json.loads(os.getenv("TERCC")))
            self.etos.config.set("tercc", tercc)
        return self.etos.config.get("tercc")

    @property
    def test_suite(self):
        """Download and return test batches.

        :return: Batches.
        :rtype: list
        """
        with self.lock:
            if self.__test_suite is None:
                tercc = self.tercc.json
                batch_uri = tercc.get("data", {}).get("batchesUri")
                json_header = {"Accept": "application/json"}
                json_response = self.etos.http.wait_for_request(
                    batch_uri,
                    headers=json_header,
                )
                response = {}
                for response in json_response:
                    break
                self.__test_suite = response
        return self.__test_suite if self.__test_suite else []

    @property
    def product(self):
        """Product name from artifact created event.

        :return: Product name.
        :rtype: str
        """
        if self.etos.config.get("product") is None:
            identity = self.artifact_created["data"].get("identity")
            purl = PackageURL.from_string(identity)
            self.etos.config.set("product", purl.name)
        return self.etos.config.get("product")

    def collect_environments(self):
        """Get environments for all test suites in this ETOS run."""
        FORMAT_CONFIG.identifier = self.tercc.meta.event_id
        downloaded = []
        status = {
            "status": "FAILURE",
            "error": "Couldn't collect any error information",
        }
        self.logger.debug(
            "Start collecting sub suite environments (timeout=%ds).",
            self.etos.config.get("WAIT_FOR_ENVIRONMENT_TIMEOUT"),
        )
        timeout = time.time() + self.etos.config.get("WAIT_FOR_ENVIRONMENT_TIMEOUT")
        while time.time() < timeout:
            with self.lock:
                status = self.environment_status.copy()
            for environment in request_environment_defined(
                self.etos, self.etos.config.get("context")
            ):
                if environment["meta"]["id"] in downloaded:
                    continue
                suite = self._download_sub_suite(environment)
                if self.error:
                    self.logger.warning("Stop collecting sub suites due to error: %r", self.error)
                    break
                downloaded.append(environment["meta"]["id"])
                if suite is None:  # Not a real sub suite environment defined event.
                    continue
                suite["id"] = environment["meta"]["id"]
                with self.lock:
                    self.__environments.setdefault(suite["test_suite_started_id"], [])
                    self.__environments[suite["test_suite_started_id"]].append(suite)
            if status["status"] == "FAILURE":
                self.logger.warning(
                    "Stop collecting sub suites due to status: %r, reason %r",
                    status["status"],
                    status.get("error"),
                )
                break
            if status["status"] != "PENDING" and len(downloaded) >= len(self.test_suite):
                # We must have found at least one environment for each test suite.
                self.environment_provider_done = True
                self.logger.debug("All sub suites have been collected")
                break
            time.sleep(5)
        if status["status"] == "FAILURE":
            with self.lock:
                self.error = EnvironmentProviderException(
                    status["error"], self.etos.config.get("task_id")
                )
                self.logger.warning("Sub suite collection exited with an error: %r", self.error)

    def _download_sub_suite(self, environment):
        """Download a sub suite from an EnvironmentDefined event.

        :param environment: Environment defined event to download from.
        :type environment: dict
        :return: Downloaded sub suite information.
        :rtype: dict
        """
        if environment["data"].get("uri") is None:
            return None
        uri = environment["data"]["uri"]
        json_header = {"Accept": "application/json"}
        json_response = self.etos.http.wait_for_request(
            uri,
            headers=json_header,
        )
        suite = {}
        for suite in json_response:
            break
        else:
            self.error = Exception("Could not download sub suite instructions")
        return suite

    def environments(self, test_suite_started_id):
        """Iterate over all environments correlated to a test suite started.

        :param test_suite_started_id: The ID to correlate environments with.
        :type test_suite_started_id: str
        """
        found = 0
        while not self.error:
            time.sleep(1)
            finished = self.environment_provider_done
            with self.lock:
                environments = self.__environments.get(test_suite_started_id, []).copy()
            for environment in environments:
                with self.lock:
                    self.__environments[test_suite_started_id].remove(environment)
                found += 1
                self.logger.debug(
                    "Sub suite environment received: %r", environment.get("test_suite_started_id")
                )
                yield environment
            if finished and found > 0:
                break
