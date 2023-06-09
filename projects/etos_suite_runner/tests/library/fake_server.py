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
"""ETOS suite runner fake server library."""
from threading import Thread
import socket
from http.server import HTTPServer


class FakeServer:
    """A fake server to be used in testing."""

    mock_server = None
    thread = None
    port = None

    def __init__(self, handler):
        """Initialize server with status name and response json.

        :param handler: Handler class for HTTP requests to the server.
        :type handler: :obj:`BaseHTTPRequestHandler`
        """
        self.handler = handler

    @staticmethod
    def __free_port():
        """Figure out a free port for localhost."""
        sock = socket.socket(socket.AF_INET, type=socket.SOCK_STREAM)
        sock.bind(("localhost", 0))
        _, port = sock.getsockname()
        sock.close()
        return port

    @property
    def host(self):
        """Host property for this fake server."""
        return f"http://localhost:{self.port}"

    def __enter__(self):
        """Figure out free port and start up a fake server in a thread."""
        self.port = self.__free_port()
        self.mock_server = HTTPServer(("localhost", self.port), self.handler)
        self.thread = Thread(target=self.mock_server.serve_forever)
        self.thread.setDaemon(True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        """Shut down fake server."""
        self.mock_server.shutdown()
        self.thread.join(timeout=10)
