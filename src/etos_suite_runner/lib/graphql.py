# Copyright 2020-2021 Axis Communications AB.
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
    ACTIVITY_TRIGGERED,
    CONFIDENCE_LEVEL,
    TEST_SUITE_STARTED,
    TEST_SUITE_FINISHED,
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


def request_activity(etos, suite_id):
    """Request an activity event from graphql.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param suite_id: ID of execution recipe triggering this activity.
    :type suite_id: str
    :return: Response from graphql or None
    :rtype: dict or None
    """
    for response in request(etos, ACTIVITY_TRIGGERED % suite_id):
        if response:
            try:
                _, activity = next(
                    etos.graphql.search_for_nodes(response, "activityTriggered")
                )
            except StopIteration:
                return None
            return activity
    return None


def request_test_suite_started(etos, activity_id):
    """Request test suite started from graphql.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param activity_id: ID of activity in which the test suites started
    :type activity_id: str
    :return: Iterator of test suite started graphql responses.
    :rtype: iterator
    """
    for response in request(etos, TEST_SUITE_STARTED % activity_id):
        if response:
            for _, test_suite_started in etos.graphql.search_for_nodes(
                response, "testSuiteStarted"
            ):
                yield test_suite_started
            return None  # StopIteration
    return None  # StopIteration


def request_test_suite_finished(etos, test_suite_ids):
    """Request test suite finished from graphql.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param test_suite_ids: list of test suite started IDs of which finished to search for.
    :type test_suite_ids: list
    :return: Iterator of test suite finished graphql responses.
    :rtype: iterator
    """
    or_query = "{'$or': ["
    or_query += ", ".join(
        [
            f"{{'links.type': 'TEST_SUITE_EXECUTION', 'links.target': '{test_suite_id}'}}"
            for test_suite_id in test_suite_ids
        ]
    )
    or_query += "]}"
    for response in request(etos, TEST_SUITE_FINISHED % or_query):
        if response:
            for _, test_suite_finished in etos.graphql.search_for_nodes(
                response, "testSuiteFinished"
            ):
                yield test_suite_finished
            return None  # StopIteration
    return None  # StopIteration


def request_confidence_level(etos, test_suite_ids):
    """Request confidence levels from graphql.

    :param etos: ETOS client instance.
    :type etos: :obj:`etos_lib.etos.Etos`
    :param test_suite_ids: list of test suite started IDs of which confidences to search for.
    :type test_suite_ids: list
    :return: Iterator of confidence level modified graphql responses.
    :rtype: iterator
    """
    or_query = "{'$or': ["
    or_query += ", ".join(
        [
            f"{{'links.type': 'CAUSE', 'links.target': '{test_suite_id}'}}"
            for test_suite_id in test_suite_ids
        ]
    )
    or_query += "]}"
    for response in request(etos, CONFIDENCE_LEVEL % or_query):
        if response:
            for _, confidence_level in etos.graphql.search_for_nodes(
                response, "confidenceLevelModified"
            ):
                yield confidence_level
            return None  # StopIteration
    return None  # StopIteration
