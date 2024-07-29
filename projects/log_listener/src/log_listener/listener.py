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
"""Log listener module."""
import json
import logging
import os
import pathlib
import threading
import time
from typing import Optional

from eiffellib.events import EiffelTestExecutionRecipeCollectionCreatedEvent

from .log_subscriber import LogSubscriber


# TODO: Temporarily using two files, one for logs and one for events.
# the log file shall be removed when the /log endpoint is being removed.
# pylint:disable=too-many-instance-attributes
class Listener(threading.Thread):
    """Listen to log messages from ETOS executions."""

    __identifier = None
    __stop = False
    rabbitmq = None
    logger = logging.getLogger(__name__)

    def __init__(self, lock: threading.Lock, log_file: pathlib.Path, event_file: pathlib.Path):
        """Initialize ETOS library."""
        super().__init__()
        self.lock = lock
        self.log_file = log_file
        self.event_file = event_file
        with self.lock:
            with self.event_file.open() as _event_file:
                self.id = len(_event_file.readlines()) + 1

    @property
    def identifier(self) -> str:
        """Get ETOS identifier from environment."""
        if self.__identifier is None:
            if os.getenv("IDENTIFIER") is not None:
                self.__identifier = os.getenv("IDENTIFIER", "Unknown")
            else:
                self.__identifier = self.tercc.meta.event_id
        return self.__identifier

    @property
    def tercc(self) -> EiffelTestExecutionRecipeCollectionCreatedEvent:
        """Test execution recipe collection created event from environment."""
        tercc = EiffelTestExecutionRecipeCollectionCreatedEvent()
        tercc.rebuild(json.loads(os.getenv("TERCC")))
        return tercc

    def new_event(self, event: dict, _: Optional[str] = None) -> None:
        """Get a new event from the internal RabbitMQ bus and write it to file."""
        if event.get("event") is None:
            event = {"event": "message", "data": event}
        self.__write(**event)

    def __write(self, event: str, data: str) -> None:
        """Write an event, and its data, to a file."""
        with self.lock:
            data = {"id": self.id, "event": event, "data": data}
            with self.event_file.open("a") as events:
                events.write(f"{json.dumps(data)}\n")

            # TODO: Temporarily writing to two files, self.log_file is to be removed when the /log
            # endpoint is being removed.
            if event.lower() == "message":
                with self.log_file.open("a") as log_file:
                    log_file.write(f"{data['data']}\n")
            self.id += 1

    def __queue_params(self) -> Optional[dict]:
        """Get queue parameters from environment."""
        queue_params = os.getenv("ETOS_RABBITMQ_QUEUE_PARAMS")
        if queue_params is not None:
            queue_params = json.loads(queue_params)
        return queue_params

    def __queue_name(self) -> str:
        """Get a queue name for ETOS logger."""
        queue_name = os.getenv("ETOS_RABBITMQ_QUEUE_NAME", "*")
        return queue_name.replace("*", self.identifier)

    def __rabbitmq_parameters(self) -> dict:
        """Parameters for a RabbitMQ subscriber."""
        ssl = os.getenv("ETOS_RABBITMQ_SSL", "true") == "true"
        return {
            "host": os.getenv("ETOS_RABBITMQ_HOST", "127.0.0.1"),
            "exchange": os.getenv("ETOS_RABBITMQ_EXCHANGE", "etos"),
            "username": os.getenv("ETOS_RABBITMQ_USERNAME", None),
            "password": os.getenv("ETOS_RABBITMQ_PASSWORD", None),
            "port": int(os.getenv("ETOS_RABBITMQ_PORT", "5672")),
            "vhost": os.getenv("ETOS_RABBITMQ_VHOST", None),
            "queue": self.__queue_name(),
            "queue_params": self.__queue_params(),
            "routing_key": f"{self.identifier}.#.#",
            "ssl": ssl,
        }

    def run(self) -> None:
        """Run listener thread."""
        self.rabbitmq = LogSubscriber(**self.__rabbitmq_parameters())
        self.rabbitmq.subscribe("*", self.new_event)
        self.rabbitmq.start()
        self.rabbitmq.wait_start()
        while self.rabbitmq.is_alive() and not self.__stop:
            time.sleep(0.1)
        self.rabbitmq.stop()
        self.rabbitmq.wait_close()

    def stop(self) -> None:
        """Stop listener thread."""
        self.__stop = True

    def clear(self) -> None:
        """Clear up RabbitMQ queue."""
        self.rabbitmq.delete_queue()
