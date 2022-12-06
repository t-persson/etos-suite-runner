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
import logging
from threading import Lock

from packageurl import PackageURL
from eiffellib.events import EiffelTestExecutionRecipeCollectionCreatedEvent
from .graphql import request_artifact_created


class ESRParameters:
    """Parameters required for ESR."""

    logger = logging.getLogger("ESRParameters")
    lock = Lock()
    __test_suite = None

    def __init__(self, etos):
        """ESR parameters instance."""
        self.etos = etos
        self.issuer = {"name": "ETOS Suite Runner"}
        self.environment_status = {"status": "NOT_STARTED", "error": None}

    def set_status(self, status, error):
        """Set environment provider status."""
        with self.lock:
            self.logger.debug("Setting environment status to %r, error %r", status, error)
            self.environment_status["status"] = status
            self.environment_status["error"] = error

    def get_status(self):
        """Get environment provider status.

        :return: Status dictionary for the environment provider.
        :rtype: dict
        """
        with self.lock:
            return self.environment_status.copy()

    @property
    def artifact_created(self):
        """Artifact under test.

        :return: Artifact created event.
        :rtype: :obj:`EiffelArtifactCreatedEvent`
        """
        if self.etos.config.get("artifact_created") is None:
            artifact_created = request_artifact_created(self.etos, self.tercc)
            self.etos.config.set("artifact_created", artifact_created)
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
                batch = tercc.get("data", {}).get("batches")
                batch_uri = tercc.get("data", {}).get("batchesUri")
                if batch is not None and batch_uri is not None:
                    raise Exception("Only one of 'batches' or 'batchesUri' shall be set")
                if batch is not None:
                    self.__test_suite = batch
                elif batch_uri is not None:
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
