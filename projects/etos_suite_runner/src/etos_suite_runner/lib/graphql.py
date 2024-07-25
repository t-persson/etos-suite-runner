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
"""Graphql query handler module."""
from .graphql_queries import (
    TEST_SUITE_STARTED,
    TEST_SUITE_FINISHED,
    ACTIVITY_TRIGGERED,
    ACTIVITY_FINISHED,
    ENVIRONMENTS,
    ARTIFACTS,
)


def request(etos, query):
    """Request graphql in a generator.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param query: Query to send to graphql.
    :type query: str
    :return: Generator
    :rtype: generator
    """
    wait_generator = etos.utils.wait(etos.graphql.execute, query=query)
    yield from wait_generator

def request_artifact_created(etos, tercc=None, artifact_id=None):
    """Fetch artifact created events from GraphQL.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param tercc: The TERCC to get artifact created from.
    :type tercc: dict
    :param artifact_id: The ID of the artifact created.
    :type artifact_id: str
    :return: Artifact created event.
    :rtype: :obj:`EiffelArtifactCreatedEvent`
    """
    if tercc is None and artifact_id is None:
        raise Exception("At least one of 'tercc' and 'artifact_id' must be provided")
    if tercc:
        query = ARTIFACTS % etos.utils.eiffel_link(tercc, "CAUSE")
    else:
        query = ARTIFACTS % artifact_id

    for response in request(etos, query):
        try:
            created_node = next(etos.utils.search(response, "node"))[1]
        except StopIteration:
            created_node = None
        if not created_node:
            continue
        return created_node
    return None


def request_test_suite_started(etos, main_suite_id):
    """Request test suite started from graphql.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param main_suite_id: ID of test suite which caused the test suites started
    :type main_suite_id: str
    :return: Iterator of test suite started graphql responses.
    :rtype: iterator
    """
    for response in request(etos, TEST_SUITE_STARTED % main_suite_id):
        if response:
            for _, test_suite_started in etos.graphql.search_for_nodes(
                response, "testSuiteStarted"
            ):
                yield test_suite_started
            return None  # StopIteration
    return None  # StopIteration


def request_test_suite_finished(etos, test_suite_started_id):
    """Request test suite started from graphql.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param test_suite_started_id: ID of test suite which caused the test suites started
    :type test_suite_started_id: str
    :return: Iterator of test suite started graphql responses.
    :rtype: iterator
    """
    for response in request(etos, TEST_SUITE_FINISHED % test_suite_started_id):
        if response:
            try:
                _, test_suite_finished = next(
                    etos.graphql.search_for_nodes(response, "testSuiteFinished")
                )
            except StopIteration:
                return None
            return test_suite_finished
    return None


def request_activity_triggered(etos, test_suite_started_id):
    """Request activity defined from graphql.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param test_suite_started_id: ID of test suite in which the activity is sent
    :type test_suite_started_id: str
    :return: Iterator of activity triggered graphql responses.
    :rtype: iterator
    """
    for response in request(etos, ACTIVITY_TRIGGERED % test_suite_started_id):
        if response:
            try:
                _, activity = next(etos.graphql.search_for_nodes(response, "activityTriggered"))
            except StopIteration:
                return None
            return activity
    return None


def request_activity_finished(etos, activity_triggered_id):
    """Request activity defined from graphql.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param activity_triggered_id: ID of activity in which the activity is sent
    :type activity_triggered_id: str
    :return: Iterator of activity finished graphql responses.
    :rtype: iterator
    """
    for response in request(etos, ACTIVITY_FINISHED % activity_triggered_id):
        if response:
            try:
                _, activity = next(etos.graphql.search_for_nodes(response, "activityFinished"))
            except StopIteration:
                return None
            return activity
    return None


def request_environment_defined(etos, activity_id):
    """Request environment defined from graphql.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param activity_id: ID of activity in which the environment defined are sent
    :type activity_id: str
    :return: Iterator of environment defined graphql responses.
    :rtype: iterator
    """
    for response in request(etos, ENVIRONMENTS % activity_id):
        if response:
            for _, environment in etos.graphql.search_for_nodes(response, "environmentDefined"):
                yield environment
            return None  # StopIteration
    return None  # StopIteration
