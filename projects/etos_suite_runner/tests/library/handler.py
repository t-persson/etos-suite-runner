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
import json
import logging
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

import mongomock
import requests
from eiffellib.events import EiffelTestSuiteFinishedEvent, EiffelTestSuiteStartedEvent
from etos_lib.lib.debug import Debug
from graphql import parse

CLIENT = mongomock.MongoClient()
DB = CLIENT["Database"]


class Handler(BaseHTTPRequestHandler):
    """HTTP handler for the fake HTTP server."""

    logger = logging.getLogger(__name__)
    requests = []
    uploads = {}
    debug = Debug()

    def __init__(self, tercc, *args, **kwargs):
        """Initialize a BaseHTTPRequestHandler. This must be initialized with functools.partial.

        Example:
            handler = functools.partial(Handler, self.tercc)
            with FakeServer(handler) as server:
                print(server.host)

        :param tercc: Test execution recipe collection for a test scenario.
        :type tercc: dict
        """
        self.tercc = tercc
        super().__init__(*args, **kwargs)

    @classmethod
    def reset(cls):
        """Reset the handler. This has to be done after each test."""
        cls.requests.clear()
        CLIENT.drop_database("Database")
        cls.uploads.clear()

    @classmethod
    def insert_to_db(cls, event):
        """Insert an event to the database.

        :param event: Event to store into database.
        :type event: :obj:`eiffellib.events.eiffel_base_event.EiffelBaseEvent`
        """
        collection = DB[event.meta.type]
        response = collection.find_one({"_id": event.meta.event_id})
        if not response:
            doc = event.json
            doc["_id"] = event.meta.event_id
            collection.insert_one(doc)

    @classmethod
    def get_from_db(cls, collection_name, query):
        """Send a query to a database collection.

        :param collection_name: The collection to query.
        :type collection_name: str
        :param query: The query to send.
        :type query: dict
        :return: a list of events from the database.
        :rtype: list
        """
        for event in cls.debug.events_published:
            cls.insert_to_db(event)
        return list(DB[collection_name].find(query))

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

    def to_graphql(self, name, data):
        """Convert an Eiffel event dictionary to a GraphQL response.

        :param name: Name of the event, in graphql.
        :type name: str
        :param data: Data to set in graphql response.
        :type data: list or None
        :return: A graphql-valid dictionary
        :rtype: dict
        """
        if data:
            edges = [{"node": d} for d in data]
            return {"data": {name: {"edges": edges}}}
        return {"data": {name: {"edges": []}}}

    def do_graphql(self, query_name, query):  # pylint:disable=too-many-return-statements
        """Handle GraphQL queries to a fake ER.

        :param query_name: The name of query (or eiffel event) from a GraphQL query.
        :type query_name: str
        :param query: The query from a GraphQL request.
        :type query: dict
        :return: JSON data mimicking an ER.
        :rtype: dict
        """
        data = None
        if query_name == "testExecutionRecipeCollectionCreated":
            data = self.get_from_db("EiffelTestExecutionRecipeCollectionCreatedEvent", query)
        if query_name == "artifactCreated":
            data = self.get_from_db("EiffelArtifactCreatedEvent", query)
        if query_name == "activityTriggered":
            data = self.get_from_db("EiffelActivityTriggeredEvent", query)
        if query_name == "activityFinished":
            data = self.get_from_db("EiffelActivityFinishedEvent", query)
            for event in data:
                # The GraphQL API changes the outcome fields since they are the same for multiple
                # events so we have to correct the data here as well.
                event["data"]["activityOutcome"] = event["data"].pop("outcome")
        if query_name == "environmentDefined":
            data = self.get_from_db("EiffelEnvironmentDefinedEvent", query)
        if query_name == "testSuiteStarted":
            data = self.get_from_db("EiffelTestSuiteStartedEvent", query)
        if query_name == "testSuiteFinished":
            data = self.get_from_db("EiffelTestSuiteFinishedEvent", query)
            for event in data:
                # The GraphQL API changes the outcome fields since they are the same for multiple
                # events so we have to correct the data here as well.
                event["data"]["testSuiteOutcome"] = event["data"].pop("outcome")
        return self.to_graphql(query_name, data)

    def fake_start_etr(self, request_data):
        """Handle the ETR start requests from the ESR.

        :param request_data: Request data from the ESR, with instructions.
        :type request_data: bytes
        """
        json_request = json.loads(request_data)
        environment = DB["EiffelEnvironmentDefinedEvent"].find_one(
            {"meta.id": json_request["environment"]["ENVIRONMENT_ID"]},
        )
        sub_suite = json.loads(self.uploads.get(urlparse(environment["data"]["uri"]).path))
        started = EiffelTestSuiteStartedEvent()
        started.data.add("name", environment["data"]["name"])
        started.links.add("CAUSE", sub_suite["test_suite_started_id"])
        started.validate()
        finished = EiffelTestSuiteFinishedEvent()
        finished.data.add("outcome", {"verdict": "PASSED", "conclusion": "SUCCESSFUL"})
        finished.links.add("TEST_SUITE_EXECUTION", started)
        finished.validate()
        Debug().events_published.append(started)
        Debug().events_published.append(finished)

    # pylint:disable=invalid-name
    def do_POST(self):
        """Handle POST requests."""
        self.store_request(self.request)
        request_data = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path.startswith("/log"):
            self.uploads[self.path] = request_data.decode()
            response = {}
        elif self.path.startswith("/etr"):
            self.fake_start_etr(request_data)
            response = {}
        else:
            try:
                query_name, query = self.get_gql_query(request_data)
            except (TypeError, KeyError):
                response = {}
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

        response = self.uploads.get(self.path)
        self.send_response(requests.codes["ok"])
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(response.encode("utf-8"))
