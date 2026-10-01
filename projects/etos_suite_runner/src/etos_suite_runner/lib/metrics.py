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
"""Bounded Suite Runner metric instruments."""

from typing import Callable, TypeVar

from opentelemetry.metrics import Meter

_RESULT = TypeVar("_RESULT")
_EVENT_CLASSES = {
    "activity_started",
    "recipe_collection_created",
    "activity_triggered",
    "activity_finished",
    "activity_canceled",
    "test_suite_started",
    "test_suite_finished",
    "shutdown",
}
_API_VERSIONS = {"v1alpha1", "v1beta1"}


class SuiteRunnerMetrics:
    """Record Suite Runner lifecycle metrics without execution identifiers."""

    def __init__(self, meter: Meter, api_version: str = "unknown") -> None:
        """Create the Suite Runner metric instruments."""
        self._api_version = self._normalize_api_version(api_version)
        self._suite_executions = meter.create_counter(
            "etos.suite_runner.suite.executions",
            unit="{execution}",
            description="Controller-managed Suite Runner terminal outcomes.",
        )
        self._suite_duration = meter.create_histogram(
            "etos.suite_runner.suite.duration",
            unit="s",
            description="Controller-managed Suite Runner execution duration.",
        )
        self._result_collections = meter.create_counter(
            "etos.suite_runner.result_collection.completed",
            unit="{collection}",
            description="Sub-suite result collection terminal outcomes.",
        )
        self._result_collection_delay = meter.create_histogram(
            "etos.suite_runner.result_collection.delay",
            unit="s",
            description="Time spent waiting for a sub-suite terminal result.",
        )
        self._event_publish_attempts = meter.create_counter(
            "etos.suite_runner.event_publish.attempts",
            unit="{attempt}",
            description="Event publish calls made by the Suite Runner boundary.",
        )
        self._event_publish_failures = meter.create_counter(
            "etos.suite_runner.event_publish.failures",
            unit="{failure}",
            description="Event publish calls that raised at the Suite Runner boundary.",
        )

    @staticmethod
    def _suite_outcome(value: str) -> str:
        """Map result values onto a bounded suite outcome vocabulary."""
        normalized = value.lower()
        if normalized in ("passed", "successful", "success"):
            return "success"
        if normalized in ("failed", "failure"):
            return "failure"
        if normalized in ("inconclusive", "unknown"):
            return "inconclusive"
        return "error"

    @staticmethod
    def _collection_outcome(value: str) -> str:
        """Map result collection values onto a bounded outcome vocabulary."""
        if value in ("received", "missing", "error"):
            return value
        return "error"

    @staticmethod
    def _event_class(value: str) -> str:
        """Map event names to the bounded publish vocabulary."""
        if value in _EVENT_CLASSES:
            return value
        return "other"

    @staticmethod
    def _normalize_api_version(value: str) -> str:
        """Keep the API-version dimension within the supported vocabulary."""
        if value in _API_VERSIONS:
            return value
        return "unknown"

    def set_api_version(self, value: str) -> None:
        """Set the version from the controller-managed TestRun schema."""
        self._api_version = self._normalize_api_version(value)

    def _base_attributes(self) -> dict[str, str]:
        """Return the bounded API-version attribute shared by all instruments."""
        return {"etos.api_version": self._api_version}

    def record_suite(self, outcome: str, duration: float) -> None:
        """Record one terminal outcome and duration for one runner execution."""
        attributes = self._base_attributes()
        attributes.update(
            {
                "etos.stage": "suite_execution",
                "etos.outcome": self._suite_outcome(outcome),
            }
        )
        self._suite_executions.add(1, attributes=attributes)
        self._suite_duration.record(max(0.0, duration), attributes=attributes)

    def record_result_collection(self, outcome: str, delay: float) -> None:
        """Record one result-collection outcome and its wait duration."""
        attributes = self._base_attributes()
        attributes.update(
            {
                "etos.stage": "result_collection",
                "etos.outcome": self._collection_outcome(outcome),
            }
        )
        self._result_collections.add(1, attributes=attributes)
        self._result_collection_delay.record(max(0.0, delay), attributes=attributes)

    def record_event_publish_attempt(self, event_class: str) -> None:
        """Count one publish API call at the Suite Runner boundary."""
        attributes = self._base_attributes()
        attributes["etos.event_class"] = self._event_class(event_class)
        self._event_publish_attempts.add(1, attributes=attributes)

    def record_event_publish_failure(self, event_class: str) -> None:
        """Count one publish API call that raised at the Suite Runner boundary."""
        attributes = self._base_attributes()
        attributes["etos.event_class"] = self._event_class(event_class)
        self._event_publish_failures.add(1, attributes=attributes)


def publish_event(
    metrics: SuiteRunnerMetrics,
    event_class: str,
    publish: Callable[[], _RESULT],
    enabled: bool = True,
) -> _RESULT:
    """Count and invoke one Suite Runner event-publisher boundary call."""
    if not enabled:
        return publish()
    metrics.record_event_publish_attempt(event_class)
    try:
        return publish()
    except Exception:
        metrics.record_event_publish_failure(event_class)
        raise
