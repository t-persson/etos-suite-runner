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
"""Log subscriber module."""
import json
from typing import Tuple
from eiffellib.subscribers import RabbitMQSubscriber


class LogSubscriber(RabbitMQSubscriber):
    """Subscriber to ETOS logs via the RabbitMQSubscriber."""

    def delete_queue(self) -> None:
        """Delete a queue.

        This functionality does not exist in RabbitMQSubscriber and is
        necessary here.
        """
        self._channel.queue_delete(queue=self.queue)

    def _call_subscribers(self, meta_type, event):
        """Call all subscriber callback methods."""
        ack = False
        at_least_one = False
        for callback in self.subscribers.get("*", []):
            callback(event, None)
        for callback in self.nackables.get("*", []):
            at_least_one = True
            response = callback(event, None)
            if response is True:
                ack = True

        if at_least_one:
            return ack
        return True

    def call(self, body: str) -> Tuple[bool, bool]:
        """Override the RabbitMQSubscriber call method to handle 'normal' non-eiffel messages."""
        try:
            json_data = json.loads(body.decode("utf-8"))
        except (json.decoder.JSONDecodeError, UnicodeDecodeError) as err:
            raise TypeError(
                f"Unable to deserialize message body ({err}), rejecting: {body!r}"
            ) from err
        try:
            ack = self._call_subscribers(None, json_data)
        except:  # pylint:disable=bare-except
            ack = False
        return ack, True
