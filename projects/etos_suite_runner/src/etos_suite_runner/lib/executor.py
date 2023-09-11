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
import os
import logging
from cryptography.fernet import Fernet
from requests.auth import HTTPBasicAuth, HTTPDigestAuth


class Executor:  # pylint:disable=too-few-public-methods
    """Executor for launching ETR."""

    logger = logging.getLogger("ESR - Executor")

    def __init__(self, etos):
        """Initialize executor.

        :param etos: ETOS library instance.
        :type etos: :obj:`etos_library.EtosLibrary`
        """
        self.etos = etos
        self.etos.config.set("build_urls", [])

    def __decrypt(self, password):
        """Decrypt a password using an encryption key.

        :param password: Password to decrypt.
        :type password: str or dict
        :return: Decrypted password
        :rtype: str
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

    def __auth(self, username, password, type="basic"):  # pylint:disable=redefined-builtin
        """Create an authentication for HTTP request.

        :param username: Username to authenticate.
        :type username: str
        :param password: Password to authenticate with.
        :type password: str
        :param type: Type of authentication. 'basic' or 'digest'.
        :type type: str
        :return: Authentication method.
        :rtype: :obj:`requests.auth`
        """
        password = self.__decrypt(password)
        if type.lower() == "basic":
            return HTTPBasicAuth(username, password)
        return HTTPDigestAuth(username, password)

    def run_tests(self, test_suite):
        """Run tests in jenkins.

        :param test_suite: Tests to execute.
        :type test_suite: dict
        """
        executor = test_suite.get("executor")
        request = executor.get("request")
        # ETOS Library, for some reason, uses the key 'verb' instead of 'method'
        # for HTTP method.
        request["verb"] = request.pop("method")
        request["as_json"] = False
        if request.get("auth"):
            request["auth"] = self.__auth(**request["auth"])

        wait_generator = self.etos.http.retry(**request)
        for response in wait_generator:
            self.logger.info("%r", response)
            self.logger.debug("%r", response.text)
            break
