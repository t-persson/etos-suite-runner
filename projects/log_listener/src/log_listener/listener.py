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
import os
import threading
import json
import logging
import pathlib
from typing import Optional
from eiffellib.events import EiffelTestExecutionRecipeCollectionCreatedEvent
from .log_subscriber import LogSubscriber


class Listener(threading.Thread):
    """Listen to log messages from ETOS executions."""

    __tercc = None
    rabbitmq = None
    logger = logging.getLogger(__name__)

    def __init__(self, lock: threading.Lock, log_file: pathlib.Path):
        """Initialize ETOS library."""
        super().__init__()
        self.identifier = self.tercc.meta.event_id
        self.lock = lock
        self.log_file = log_file

    @property
    def tercc(self) -> EiffelTestExecutionRecipeCollectionCreatedEvent:
        """Test execution recipe collection created event from environment."""
        if self.__tercc is None:
            tercc = EiffelTestExecutionRecipeCollectionCreatedEvent()
            tercc.rebuild(json.loads(os.getenv("TERCC")))
            self.__tercc = tercc
        return self.__tercc

    def new_message(self, message: dict, _: Optional[str] = None) -> None:
        """Handle new log messages from ETOS."""
        self.logger.info(message)
        with self.lock:
            with self.log_file.open("a") as log_file:
                log_file.write(f"{json.dumps(message)}\n")

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
            "routing_key": f"{self.identifier}.log.#",
            "ssl": ssl,
        }

    def run(self) -> None:
        """Run listener thread."""
        self.rabbitmq = LogSubscriber(**self.__rabbitmq_parameters())
        self.rabbitmq.subscribe("*", self.new_message)
        self.rabbitmq.start()
        self.rabbitmq.wait_start()
        self.rabbitmq.wait_close()

    def stop(self) -> None:
        """Stop listener thread."""
        self.rabbitmq.stop()

    def clear(self) -> None:
        """Clear up RabbitMQ queue."""
        self.rabbitmq.delete_queue()
