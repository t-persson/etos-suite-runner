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
"""Regression tests for custom-resource API versions."""

import json
from unittest.mock import Mock, call, patch

from etos_lib.kubernetes.schemas.v1beta1.environment import Environment as V1Beta1Environment
from etos_lib.kubernetes.schemas.v1beta1.testrun import Suite as V1Beta1Suite
from etos_suite_runner.lib.esr_parameters import ESRParameters, SuiteDefinition
from etos_suite_runner.lib.suite import SubSuite, TestSuite


def test_testrun_and_environment_clients_use_v1beta1(monkeypatch):
    """Fetch TestRun and Environment resources through the v1beta1 API."""
    monkeypatch.setattr(ESRParameters, "_ESRParameters__test_suite", None)
    monkeypatch.setenv("TESTRUN", "testrun-id")
    etos = Mock()
    etos.config.get.side_effect = lambda key: {"testrun_id": "testrun-id"}.get(key)
    parameters = ESRParameters(etos)
    environment_client = Mock()
    environment_client.namespace = "namespace"
    environment_client.client.get.return_value.to_dict.return_value = {
        "items": [
            {
                "metadata": {"name": "environment"},
                "spec": {
                    "name": "environment",
                    "id": "environment-id",
                    "testrunID": "testrun-id",
                    "mainSuiteID": "suite-id",
                    "artifact": "artifact-id",
                    "context": "context",
                    "testRunner": "test-runner",
                    "providers": {},
                    "iut": {},
                    "executor": {},
                    "logArea": {},
                    "testExecutions": [],
                },
            }
        ]
    }
    testrun = Mock()
    testrun.get.return_value.to_dict.return_value = {
        "spec": {
            "name": "test-suite",
            "suites": [
                {
                    "testExecutions": [
                        {
                            "id": "execution-id",
                            "testCase": {"id": "test-case-id", "repository": "repository"},
                            "execution": {
                                "command": "run-tests",
                                "checkout": ["checkout"],
                                "preExecution": ["setup"],
                            },
                            "environment": {
                                "testRunner": "test-runner",
                                "environmentVariables": {"KEY": "value"},
                            },
                        }
                    ],
                    "dataset": {"key": "value"},
                }
            ],
        }
    }

    with (
        patch("etos_suite_runner.lib.esr_parameters.Kubernetes") as kubernetes,
        patch("etos_suite_runner.lib.esr_parameters.Environment", return_value=environment_client),
        patch("etos_suite_runner.lib.esr_parameters.TestRun", return_value=testrun),
    ):
        environments = parameters.environments("suite-id")
        assert environments[0].apiVersion == "etos.eiffel-community.github.io/v1beta1"
        suite = parameters.test_suite[0]
        testrun.get.return_value.to_dict.assert_called_once_with()
        assert isinstance(suite, SuiteDefinition)
        assert suite.name == "test-suite"
        assert isinstance(suite.suite, V1Beta1Suite)
        assert suite.suite.testExecutions[0].id == "execution-id"
        assert suite.suite.testExecutions[0].execution.command == "run-tests"
        assert suite.suite.dataset == {"key": "value"}

    assert kubernetes.call_args_list == [
        call(version="v1beta1"),
        call(version="v1beta1"),
    ]

    environment = SubSuite(
        etos,
        {"executor": {"id": "environment-id"}},
        "suite-id",
        controller=True,
    )
    with (
        patch("etos_suite_runner.lib.suite.Kubernetes") as kubernetes,
        patch("etos_suite_runner.lib.suite.Environment") as environment_resource,
    ):
        environment_resource.return_value.delete.return_value = True
        assert environment._delete_environment()

    kubernetes.assert_called_once_with(version="v1beta1")


def test_tercc_recipes_convert_to_v1beta1_suites(monkeypatch):
    """Keep TERCC input while using the v1beta1 suite model internally."""
    monkeypatch.setattr(ESRParameters, "_ESRParameters__test_suite", None)
    monkeypatch.delenv("TESTRUN", raising=False)
    monkeypatch.setenv(
        "TERCC",
        json.dumps(
            {
                "data": {
                    "batches": [
                        {
                            "name": "legacy-suite",
                            "recipes": [
                                {
                                    "id": "execution-id",
                                    "testCase": {"id": "test-case-id", "tracker": "repository"},
                                    "constraints": [
                                        {"key": "COMMAND", "value": "run-tests"},
                                        {"key": "EXECUTE", "value": ["setup"]},
                                        {"key": "CHECKOUT", "value": ["checkout"]},
                                        {"key": "TEST_RUNNER", "value": "test-runner"},
                                        {"key": "ENVIRONMENT", "value": {"KEY": "value"}},
                                        {"key": "PARAMETERS", "value": {}},
                                    ],
                                }
                            ],
                        }
                    ]
                }
            }
        ),
    )
    etos = Mock()
    etos.config.get.return_value = None
    parameters = ESRParameters(etos)

    suite = parameters.test_suite[0]

    assert suite.name == "legacy-suite"
    assert isinstance(suite.suite, V1Beta1Suite)
    assert suite.suite.testExecutions[0].execution.command == "run-tests"
    assert suite.suite.testExecutions[0].environment.environmentVariables == {"KEY": "value"}


def test_controller_suite_uses_v1beta1_environment_id(monkeypatch):
    """Use the v1beta1 Environment spec's id when collecting sub-suites."""
    environment = V1Beta1Environment.model_validate(
        {
            "metadata": {"name": "environment"},
            "spec": {
                "name": "environment",
                "id": "environment-id",
                "testrunID": "testrun-id",
                "mainSuiteID": "suite-id",
                "artifact": "artifact-id",
                "context": "context",
                "testRunner": "test-runner",
                "providers": {},
                "iut": {},
                "executor": {"request": None},
                "logArea": {},
                "testExecutions": [],
            },
        }
    )
    etos = Mock()
    etos.config.get.side_effect = lambda key: 1 if key == "WAIT_FOR_ENVIRONMENT_TIMEOUT" else None
    parameters = Mock()
    parameters.etos_controller = True
    parameters.environments.return_value = [environment]
    parameters.get_status.return_value = {"status": "SUCCESS"}
    suite = TestSuite(
        etos,
        parameters,
        SuiteDefinition("suite", V1Beta1Suite(testExecutions=[])),
        "suite-id",
    )

    with patch("etos_suite_runner.lib.suite.time.sleep"):
        sub_suites = list(suite.sub_suite_environments)

    assert sub_suites == [
        {"executor": {"request": None}, "id": "environment-id", "name": "environment"}
    ]
