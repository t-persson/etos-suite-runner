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
"""ETOS suite runner request handler."""
import os
import json
import logging
from http.server import BaseHTTPRequestHandler
from uuid import uuid4

from graphql import parse
from etos_lib.lib.debug import Debug
import requests


class Handler(BaseHTTPRequestHandler):
    """HTTP handler for the fake HTTP server."""

    logger = logging.getLogger(__name__)
    requests = []
    sub_suites_and_main_suites = {}
    environments = {}
    suite_runner_ids = []
    activities = {}

    def __init__(self, tercc, *args, **kwargs):
        """Initialize a BaseHTTPRequestHandler. This must be initialized with functools.partial.

        Example:
            handler = functools.partial(Handler, self.tercc)
            with FakeServer(handler) as server:
                print(server.host)

        :param tercc: Test execution recipe collection for a test scenario.
        :type tercc: dict
        """
        self.debug = Debug()
        self.tercc = tercc
        super().__init__(*args, **kwargs)

    @classmethod
    def reset(cls):
        """Reset the handler. This has to be done after each test."""
        cls.requests.clear()
        cls.sub_suites_and_main_suites.clear()
        cls.environments.clear()
        cls.activities.clear()
        cls.suite_runner_ids.clear()

    @property
    def main_suites(self):
        """Test suites started sent by ESR.

        :return: A list of test suite started stored in the ETOS library debug module.
        :rtype: list
        """
        started = []
        for event in self.debug.events_published:
            if event.meta.type == "EiffelTestSuiteStartedEvent":
                started.append(event)
        return started

    def store_request(self, data):
        """Store a request for testing purposes.

        :param data: Request to store.
        :type data: obj:`http.Request`
        """
        if self.requests is not None:
            self.requests.append(data)

    def get_gql_query(self, request_data):
        """Parse request data in order to get a GraphQL query string.

        :param request_data: Data to parse query string from.
        :type request_data: byte
        :return: The GraphQL query string.
        :rtype: tuple
        """
        data_dict = json.loads(request_data)
        parsed = parse(data_dict["query"]).to_dict()
        for definition in parsed.get("definitions", []):
            for selection in definition.get("selection_set", {}).get("selections", []):
                query_name = selection.get("name", {}).get("value")
                for argument in selection.get("arguments", []):
                    if argument.get("name", {}).get("value") == "search":
                        return query_name, json.loads(
                            argument.get("value", {}).get("value").replace("'", '"')
                        )
        raise TypeError("Not a valid GraphQL query")

    def artifact_created(self):
        """Artifact under test.

        :return: A GraphQL response for an artifact.
        :rtype: dict
        """
        return {
            "data": {
                "artifactCreated": {
                    "edges": [
                        {
                            "node": {
                                "data": {
                                    "identity": "pkg:etos/suite-runner",
                                },
                                "links": [],
                                "meta": {
                                    "id": "b44e0d4a-bc88-4c2a-b808-d336448c959e",
                                    "time": 1664263414557,
                                    "type": "EiffelArtifactCreatedEvent",
                                    "version": "3.0.0",
                                },
                            }
                        }
                    ]
                }
            }
        }

    def environment_defined(self, query):
        """Create environment defined events for all expected sub suites.

        :param query: GraphQL query string.
        :type query: dict
        :return: A GraphQL response for several environment defined.
        :rtype: dict
        """
        if self.suite_runner_ids and self.main_suites:
            activity = None
            for activity_id, activity_event in self.activities.items():
                if activity_id == query["links.target"]:
                    activity = activity_event
            if activity is None:
                return {"data": {"environmentDefined": {"edges": []}}}
            activity_id = activity["meta"]["id"]
            if self.environments.get(activity_id):
                return {"data": {"environmentDefined": {"edges": self.environments[activity_id]}}}
            link_id = None
            for link in activity["links"]:
                if link["type"] == "CONTEXT":
                    link_id = link["target"]
                    break
            self.environments.setdefault(activity_id, [])

            for suite in self.main_suites:
                if suite.meta.event_id != link_id:
                    continue
                host = os.getenv("ETOS_ENVIRONMENT_PROVIDER")
                tercc = None
                index = None
                for number, batch in enumerate(self.tercc["data"]["batches"]):
                    if batch["name"] == suite.data.data.get("name"):
                        index = number
                        tercc = batch
                for subindex, _ in enumerate(tercc["recipes"]):
                    sub_suite_name = f"{suite.data.data.get('name')}_SubSuite_{subindex+1}"
                    self.environments[activity_id].append(
                        {
                            "node": {
                                "data": {
                                    "name": sub_suite_name,
                                    "uri": f"{host}/sub_suite/{index}/{subindex}",
                                },
                                "meta": {"id": str(uuid4())},
                            }
                        }
                    )
            return {"data": {"environmentDefined": {"edges": self.environments[activity_id]}}}
        return {"data": {"environmentDefined": {"edges": []}}}

    def test_suite_started(self, query):
        """Create a test suite started for sub suites based on ESR suites.

        :param query: GraphQL query string.
        :type query: dict
        :return: A graphql response with a test suite started for a "started" sub suite.
        :rtype: dict
        """
        edges = []
        for key, values in self.sub_suites_and_main_suites.items():
            if key == query["links.target"]:
                for value in values:
                    edges.append(
                        {
                            "node": {
                                "data": {"name": value["name"]},
                                "meta": {"id": value["id"]},
                            }
                        }
                    )
                break
        return {"data": {"testSuiteStarted": {"edges": edges}}}

    def test_suite_finished(self, query):
        """Create a test suite finished event based on sub suites and ESR suites.

        :param query: GraphQL query string.
        :type query: dict
        :return: A graphql response with a test suite finished for a "started" sub suite.
        :rtype: dict
        """
        edges = []
        for key, values in self.sub_suites_and_main_suites.items():
            if key == query["links.target"]:
                for value in values:
                    edges.append(
                        {
                            "node": {
                                "data": {"testSuiteOutcome": {"verdict": "PASSED"}},
                                "meta": {"id": value["finished"]},
                            }
                        }
                    )
                break
        return {"data": {"testSuiteFinished": {"edges": edges}}}

    def activity_triggered(self, query):
        """Create an activity triggered event.

        :param query: GraphQL query string.
        :type query: dict
        :return: A graphql response with an activity triggered for a triggered environment provider.
        :rtype: dict
        """
        suite_id = query["links.target"]
        event_id = str(uuid4())
        activity = {
            "data": {
                "activityTriggered": {
                    "meta": {"id": event_id},
                    "links": [{"type": "CONTEXT", "target": suite_id}],
                }
            }
        }
        self.activities[event_id] = activity["data"]["activityTriggered"]
        return activity

    def activity_finished(self, query):
        """Create an activity finished event.

        :param query: GraphQL query string.
        :type query: dict
        :return: A graphql response with an activity finished for a "finished" activity.
        :rtype: dict
        """
        if self.environments:
            for activity in self.activities:
                if activity == query["links.target"]:
                    return {
                        "data": {
                            "activityFinished": {
                                "edges": [
                                    {
                                        "node": {
                                            "meta": {"id": str(uuid4())},
                                            "data": {
                                                "activityOutcome": {
                                                    "conclusion": "SUCCESSFUL",
                                                    "description": None,
                                                }
                                            },
                                        }
                                    }
                                ]
                            }
                        }
                    }
        return {"data": {"activityFinished": {"edges": []}}}

    def do_graphql(self, query_name, query):  # pylint:disable=too-many-return-statements
        """Handle GraphQL queries to a fake ER.

        :param query_name: The name of query (or eiffel event) from a GraphQL query.
        :type query_name: str
        :param query: The query from a GraphQL request.
        :type query: dict
        :return: JSON data mimicking an ER.
        :rtype: dict
        """
        if query_name == "artifactCreated":
            return self.artifact_created()
        if query_name == "activityTriggered":
            return self.activity_triggered(query)
        if query_name == "activityFinished":
            return self.activity_finished(query)
        if query_name == "environmentDefined":
            return self.environment_defined(query)
        if query_name == "testSuiteStarted":
            return self.test_suite_started(query)
        if query_name == "testSuiteFinished":
            return self.test_suite_finished(query)
        return None

    def sub_suite(self):
        """Get fake sub suite information mimicking the ETOS environment provider.

        :return: Sub suite definitions that the ESR can act upon.
        :rtype: dict
        """
        subindex = int(self.path.split("/")[-1])
        index = int(self.path.split("/")[-2])
        sub_suite = self.tercc["data"]["batches"][index].copy()
        for main_suite in self.main_suites:
            if main_suite.data.data.get("name") == sub_suite["name"]:
                main_suite_id = main_suite.meta.event_id

        sub_suite["name"] = f"{sub_suite['name']}_SubSuite_{subindex+1}"
        host = os.getenv("ETOS_ENVIRONMENT_PROVIDER")
        sub_suite["test_suite_started_id"] = main_suite_id
        self.sub_suites_and_main_suites.setdefault(main_suite_id, [])
        self.sub_suites_and_main_suites[main_suite_id].append(
            {
                "name": sub_suite["name"],
                "id": main_suite_id,
                "finished": str(uuid4()),
            }
        )
        sub_suite["executor"] = {"request": {"method": "GET", "url": f"{host}/etr"}}
        return sub_suite

    def do_environment_provider_post(self, request_data):
        """Handle POST requests to a fake environment provider.

        :return: JSON data mimicking the ETOS environment provider.
        :rtype: dict
        """
        json_data = json.loads(request_data)
        if json_data.get("suite_runner_ids") is not None and not self.suite_runner_ids:
            self.suite_runner_ids.extend(json_data["suite_runner_ids"].split(","))
        return {"result": "success", "data": {"id": "12345"}}

    def do_environment_provider_get(self):
        """Handle GET requests to a fake environment provider.

        :return: JSON data mimicking the ETOS environment provider.
        :rtype: dict
        """
        if self.path.startswith("/sub_suite"):
            return self.sub_suite()
        if self.path == "/etr" or "?single_release" in self.path or "?release" in self.path:
            # These are not being tested, just return SUCCESS.
            return {"status": "SUCCESS"}
        if self.path == "/?id=12345":
            if self.environments:
                return {"status": "SUCCESS"}
            return {"status": "PENDING"}
        return None

    # pylint:disable=invalid-name
    def do_POST(self):
        """Handle POST requests."""
        self.store_request(self.request)
        request_data = self.rfile.read(int(self.headers["Content-Length"]))
        try:
            query_name, query = self.get_gql_query(request_data)
        except (TypeError, KeyError):
            response = self.do_environment_provider_post(request_data)
        else:
            response = self.do_graphql(query_name, query)

        self.send_response(requests.codes["ok"])
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()

        response_content = json.dumps(response)
        self.wfile.write(response_content.encode("utf-8"))

    # pylint:disable=invalid-name
    def do_GET(self):
        """Handle GET requests."""
        self.store_request(self.request)
        response = self.do_environment_provider_get()

        self.send_response(requests.codes["ok"])
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()

        response_content = json.dumps(response)
        self.wfile.write(response_content.encode("utf-8"))
