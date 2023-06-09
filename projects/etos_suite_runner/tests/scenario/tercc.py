# Copyright 2022 Axis Communications AB.
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
"""TERCCs to be used in testing."""


PERMUTATION_TERCC = {
    "data": {
        "selectionStrategy": {"id": "6e922b03-1323-42ca-9cf8-34427ea13f2b"},
        "batches": [
            {
                "name": "PermutatedSuite1",
                "priority": 1,
                "recipes": [
                    {
                        "id": "ce8a900d-7921-4c0f-aac4-cc08801e074f",
                        "testCase": {
                            "id": "test_permutation",
                            "tracker": "",
                            "uri": "",
                        },
                        "constraints": [
                            {"key": "ENVIRONMENT", "value": {}},
                            {"key": "PARAMETERS", "value": {}},
                            {"key": "COMMAND", "value": "exit 0"},
                            {"key": "EXECUTE", "value": []},
                            {
                                "key": "CHECKOUT",
                                "value": ["git clone https://github.com/eiffel-community/etos.git"],
                            },
                            {
                                "key": "TEST_RUNNER",
                                "value": "registry.nordix.org/eiffel/etos-python-test-runner:3.9.0",
                            },
                        ],
                    }
                ],
            },
            {
                "name": "PermutatedSuite2",
                "priority": 1,
                "recipes": [
                    {
                        "id": "ce8a900d-7921-4c0f-aac4-cc08801e074f",
                        "testCase": {
                            "id": "test_permutation",
                            "tracker": "",
                            "uri": "",
                        },
                        "constraints": [
                            {"key": "ENVIRONMENT", "value": {}},
                            {"key": "PARAMETERS", "value": {}},
                            {"key": "COMMAND", "value": "exit 0"},
                            {"key": "EXECUTE", "value": []},
                            {
                                "key": "CHECKOUT",
                                "value": ["git clone https://github.com/eiffel-community/etos.git"],
                            },
                            {
                                "key": "TEST_RUNNER",
                                "value": "registry.nordix.org/eiffel/etos-python-test-runner:3.9.0",
                            },
                        ],
                    }
                ],
            },
        ],
    },
    "meta": {
        "type": "EiffelTestExecutionRecipeCollectionCreatedEvent",
        "id": "6e8ec0be-3299-4242-b07c-1843113c350f",
        "time": 1664260578384,
        "version": "4.1.1",
    },
    "links": [{"type": "CAUSE", "target": "349f9bf9-0fc7-4dd4-b641-ac5f1c9ea7aa"}],
}


PERMUTATION_TERCC_SUB_SUITES = {
    "data": {
        "selectionStrategy": {"id": "6e922b03-1323-42ca-9cf8-34427ea13f2b"},
        "batches": [
            {
                "name": "PermutatedSuite1",
                "priority": 1,
                "recipes": [
                    {
                        "id": "ce8a900d-7921-4c0f-aac4-cc08801e074f",
                        "testCase": {
                            "id": "test_permutation",
                            "tracker": "",
                            "uri": "",
                        },
                        "constraints": [
                            {"key": "ENVIRONMENT", "value": {}},
                            {"key": "PARAMETERS", "value": {}},
                            {"key": "COMMAND", "value": "exit 0"},
                            {"key": "EXECUTE", "value": []},
                            {
                                "key": "CHECKOUT",
                                "value": ["git clone https://github.com/eiffel-community/etos.git"],
                            },
                            {
                                "key": "TEST_RUNNER",
                                "value": "registry.nordix.org/eiffel/etos-python-test-runner:3.9.0",
                            },
                        ],
                    },
                    {
                        "id": "287c335f-bb58-413c-90f6-1766ca2bec74",
                        "testCase": {
                            "id": "test_permutation",
                            "tracker": "",
                            "uri": "",
                        },
                        "constraints": [
                            {"key": "ENVIRONMENT", "value": {}},
                            {"key": "PARAMETERS", "value": {}},
                            {"key": "COMMAND", "value": "exit 0"},
                            {"key": "EXECUTE", "value": []},
                            {
                                "key": "CHECKOUT",
                                "value": ["git clone https://github.com/eiffel-community/etos.git"],
                            },
                            {
                                "key": "TEST_RUNNER",
                                "value": "registry.nordix.org/eiffel/etos-python-test-runner:3.9.0",
                            },
                        ],
                    },
                ],
            },
            {
                "name": "PermutatedSuite2",
                "priority": 1,
                "recipes": [
                    {
                        "id": "ce8a900d-7921-4c0f-aac4-cc08801e074f",
                        "testCase": {
                            "id": "test_permutation",
                            "tracker": "",
                            "uri": "",
                        },
                        "constraints": [
                            {"key": "ENVIRONMENT", "value": {}},
                            {"key": "PARAMETERS", "value": {}},
                            {"key": "COMMAND", "value": "exit 0"},
                            {"key": "EXECUTE", "value": []},
                            {
                                "key": "CHECKOUT",
                                "value": ["git clone https://github.com/eiffel-community/etos.git"],
                            },
                            {
                                "key": "TEST_RUNNER",
                                "value": "registry.nordix.org/eiffel/etos-python-test-runner:3.9.0",
                            },
                        ],
                    },
                    {
                        "id": "0377c65c-70c6-4979-a230-e78873adab69",
                        "testCase": {
                            "id": "test_permutation",
                            "tracker": "",
                            "uri": "",
                        },
                        "constraints": [
                            {"key": "ENVIRONMENT", "value": {}},
                            {"key": "PARAMETERS", "value": {}},
                            {"key": "COMMAND", "value": "exit 0"},
                            {"key": "EXECUTE", "value": []},
                            {
                                "key": "CHECKOUT",
                                "value": ["git clone https://github.com/eiffel-community/etos.git"],
                            },
                            {
                                "key": "TEST_RUNNER",
                                "value": "registry.nordix.org/eiffel/etos-python-test-runner:3.9.0",
                            },
                        ],
                    },
                ],
            },
        ],
    },
    "meta": {
        "type": "EiffelTestExecutionRecipeCollectionCreatedEvent",
        "id": "6e8ec0be-3299-4242-b07c-1843113c350f",
        "time": 1664260578384,
        "version": "4.1.1",
    },
    "links": [{"type": "CAUSE", "target": "349f9bf9-0fc7-4dd4-b641-ac5f1c9ea7aa"}],
}


TERCC = {
    "data": {
        "selectionStrategy": {"id": "6e922b03-1323-42ca-9cf8-34427ea13f2b"},
        "batches": [
            {
                "name": "Suite",
                "priority": 1,
                "recipes": [
                    {
                        "id": "ce8a900d-7921-4c0f-aac4-cc08801e074f",
                        "testCase": {
                            "id": "test_permutation",
                            "tracker": "",
                            "uri": "",
                        },
                        "constraints": [
                            {"key": "ENVIRONMENT", "value": {}},
                            {"key": "PARAMETERS", "value": {}},
                            {"key": "COMMAND", "value": "exit 0"},
                            {"key": "EXECUTE", "value": []},
                            {
                                "key": "CHECKOUT",
                                "value": ["git clone https://github.com/eiffel-community/etos.git"],
                            },
                            {
                                "key": "TEST_RUNNER",
                                "value": "registry.nordix.org/eiffel/etos-python-test-runner:3.9.0",
                            },
                        ],
                    }
                ],
            },
        ],
    },
    "meta": {
        "type": "EiffelTestExecutionRecipeCollectionCreatedEvent",
        "id": "6e8ec0be-3299-4242-b07c-1843113c350f",
        "time": 1664260578384,
        "version": "4.1.1",
    },
    "links": [{"type": "CAUSE", "target": "349f9bf9-0fc7-4dd4-b641-ac5f1c9ea7aa"}],
}


TERCC_SUB_SUITES = {
    "data": {
        "selectionStrategy": {"id": "6e922b03-1323-42ca-9cf8-34427ea13f2b"},
        "batches": [
            {
                "name": "Suite",
                "priority": 1,
                "recipes": [
                    {
                        "id": "ce8a900d-7921-4c0f-aac4-cc08801e074f",
                        "testCase": {
                            "id": "test_regular_scenario",
                            "tracker": "",
                            "uri": "",
                        },
                        "constraints": [
                            {"key": "ENVIRONMENT", "value": {}},
                            {"key": "PARAMETERS", "value": {}},
                            {"key": "COMMAND", "value": "exit 0"},
                            {"key": "EXECUTE", "value": []},
                            {
                                "key": "CHECKOUT",
                                "value": ["git clone https://github.com/eiffel-community/etos.git"],
                            },
                            {
                                "key": "TEST_RUNNER",
                                "value": "registry.nordix.org/eiffel/etos-python-test-runner:3.9.0",
                            },
                        ],
                    },
                    {
                        "id": "4efdd599-d7a1-4d4b-ae8c-d781cd1fd658",
                        "testCase": {
                            "id": "test_regular_scenario",
                            "tracker": "",
                            "uri": "",
                        },
                        "constraints": [
                            {"key": "ENVIRONMENT", "value": {}},
                            {"key": "PARAMETERS", "value": {}},
                            {"key": "COMMAND", "value": "exit 0"},
                            {"key": "EXECUTE", "value": []},
                            {
                                "key": "CHECKOUT",
                                "value": ["git clone https://github.com/eiffel-community/etos.git"],
                            },
                            {
                                "key": "TEST_RUNNER",
                                "value": "registry.nordix.org/eiffel/etos-python-test-runner:3.9.0",
                            },
                        ],
                    },
                ],
            },
        ],
    },
    "meta": {
        "type": "EiffelTestExecutionRecipeCollectionCreatedEvent",
        "id": "6e8ec0be-3299-4242-b07c-1843113c350f",
        "time": 1664260578384,
        "version": "4.1.1",
    },
    "links": [{"type": "CAUSE", "target": "349f9bf9-0fc7-4dd4-b641-ac5f1c9ea7aa"}],
}


TERCC_EMPTY = {
    "data": {
        "selectionStrategy": {"id": "6e922b03-1323-42ca-9cf8-34427ea13f2b"},
        "batches": [
            {
                "name": "Suite",
                "priority": 1,
                "recipes": [],
            },
        ],
    },
    "meta": {
        "type": "EiffelTestExecutionRecipeCollectionCreatedEvent",
        "id": "6e8ec0be-3299-4242-b07c-1843113c350f",
        "time": 1664260578384,
        "version": "4.1.1",
    },
    "links": [{"type": "CAUSE", "target": "349f9bf9-0fc7-4dd4-b641-ac5f1c9ea7aa"}],
}
