"""OpenTelemetry metric contract tests."""

from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from etos_suite_runner import shutdown_metrics
from etos_suite_runner.lib.esr_parameters import ESRParameters
from etos_suite_runner.lib.events import EventPublisher
from etos_suite_runner.lib.metrics import SuiteRunnerMetrics


@pytest.fixture(name="metrics_reader")
def _metrics_reader_fixture():
    """Create isolated Suite Runner instruments backed by an in-memory reader."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    yield SuiteRunnerMetrics(provider.get_meter("etos_suite_runner.tests")), reader
    provider.shutdown()


def _metrics_by_name(reader):
    """Return exported metrics indexed by instrument name."""
    return {
        metric.name: metric
        for resource_metrics in reader.get_metrics_data().resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


def test_suite_execution_records_one_outcome_and_duration(metrics_reader):
    """Record exactly one bounded terminal outcome and a duration in seconds."""
    metrics, reader = metrics_reader
    metrics.record_suite("Passed", 2.5)

    exported = _metrics_by_name(reader)
    assert set(exported) == {
        "etos.suite_runner.suite.executions",
        "etos.suite_runner.suite.duration",
    }
    execution = exported["etos.suite_runner.suite.executions"]
    duration = exported["etos.suite_runner.suite.duration"]
    assert execution.unit == "{execution}"
    assert execution.data.data_points[0].value == 1
    assert execution.data.data_points[0].attributes == {
        "etos.api_version": "unknown",
        "etos.stage": "suite_execution",
        "etos.outcome": "success",
    }
    assert duration.unit == "s"
    assert duration.data.data_points[0].count == 1
    assert duration.data.data_points[0].sum == pytest.approx(2.5)


def test_result_collection_distinguishes_received_missing_and_error(metrics_reader):
    """Count one result-collection completion per terminal wait state."""
    metrics, reader = metrics_reader
    metrics.record_result_collection("received", 0.75)
    metrics.record_result_collection("missing", 3.0)
    metrics.record_result_collection("error", 1.25)

    exported = _metrics_by_name(reader)
    assert exported["etos.suite_runner.result_collection.completed"].unit == "{collection}"
    assert exported["etos.suite_runner.result_collection.delay"].unit == "s"
    completions = exported["etos.suite_runner.result_collection.completed"].data.data_points
    delays = exported["etos.suite_runner.result_collection.delay"].data.data_points
    assert {point.attributes["etos.outcome"]: point.value for point in completions} == {
        "received": 1,
        "missing": 1,
        "error": 1,
    }
    assert {point.attributes["etos.outcome"]: point.sum for point in delays} == {
        "received": pytest.approx(0.75),
        "missing": pytest.approx(3.0),
        "error": pytest.approx(1.25),
    }
    assert all(point.count == 1 for point in delays)
    assert all(point.attributes["etos.stage"] == "result_collection" for point in completions)
    assert all(point.attributes["etos.api_version"] == "unknown" for point in completions)


def test_event_publisher_counts_failed_attempt_without_retry(metrics_reader):
    """Count one failed boundary call and do not claim visibility into library retries."""
    metrics, reader = metrics_reader
    publisher = EventPublisher.__new__(EventPublisher)
    publisher.disabled = False
    publisher.identifier = "private-testrun-id"
    publisher.metrics_enabled = True
    publisher.v1_publisher = SimpleNamespace(
        running=True,
        is_alive=Mock(return_value=False),
        send_event=Mock(side_effect=RuntimeError("private error")),
    )
    publisher.v2_publisher = None

    with patch("etos_suite_runner.lib.events.METRICS", metrics):
        with pytest.raises(RuntimeError, match="private error"):
            publisher.publish_shutdown(
                {"conclusion": "Failed", "verdict": "Inconclusive", "description": "private"}
            )

    exported = _metrics_by_name(reader)
    attempts = exported["etos.suite_runner.event_publish.attempts"]
    failures = exported["etos.suite_runner.event_publish.failures"]
    assert attempts.data.data_points[0].value == 1
    assert failures.data.data_points[0].value == 1
    assert attempts.data.data_points[0].attributes == {
        "etos.api_version": "unknown",
        "etos.event_class": "shutdown",
    }
    assert failures.data.data_points[0].attributes == attempts.data.data_points[0].attributes
    assert "etos.suite_runner.event_publish.redeliveries" not in exported
    publisher.v1_publisher.send_event.assert_called_once()


def test_event_publisher_counts_each_configured_boundary_once(metrics_reader):
    """Count legacy and message-bus publish calls independently without payload dimensions."""
    metrics, reader = metrics_reader
    publisher = EventPublisher.__new__(EventPublisher)
    publisher.disabled = False
    publisher.identifier = "private-testrun-id"
    publisher.metrics_enabled = True
    publisher.v1_publisher = SimpleNamespace(
        running=True, is_alive=Mock(return_value=False), send_event=Mock()
    )
    publisher.v2_publisher = SimpleNamespace(publish=Mock())

    with patch("etos_suite_runner.lib.events.METRICS", metrics):
        publisher.publish_shutdown(
            {"conclusion": "Successful", "verdict": "Passed", "description": "private"}
        )

    exported = _metrics_by_name(reader)
    attempts = exported["etos.suite_runner.event_publish.attempts"].data.data_points
    assert len(attempts) == 1
    assert attempts[0].value == 2
    assert attempts[0].attributes == {
        "etos.api_version": "unknown",
        "etos.event_class": "shutdown",
    }
    assert "etos.suite_runner.event_publish.failures" not in exported
    publisher.v1_publisher.send_event.assert_called_once()
    publisher.v2_publisher.publish.assert_called_once()


@pytest.mark.parametrize("api_version", ["v1alpha1", "v1beta1"])
def test_api_version_and_namespaced_attributes_cover_every_metric(metrics_reader, api_version):
    """Read the version from TestRun schema and attach it to every metric."""
    metrics, reader = metrics_reader
    test_run = SimpleNamespace(spec=SimpleNamespace(schemaVersion=api_version))
    with (
        patch.dict("os.environ", {"IDENTIFIER": "controller-id", "TESTRUN": "test-run"}),
        patch("etos_suite_runner.lib.esr_parameters.Kubernetes"),
        patch("etos_suite_runner.lib.esr_parameters.TestRun") as test_run_type,
    ):
        test_run_type.return_value.get.return_value = test_run
        params = ESRParameters(Mock())
        metrics.set_api_version(params.api_version)
    test_run_type.return_value.get.assert_called_once_with("test-run")
    metrics.record_suite("Passed", 1.0)
    metrics.record_result_collection("received", 0.5)
    metrics.record_event_publish_attempt("activity_started")
    metrics.record_event_publish_failure("activity_started")

    exported = _metrics_by_name(reader)
    assert set(exported) == {
        "etos.suite_runner.suite.executions",
        "etos.suite_runner.suite.duration",
        "etos.suite_runner.result_collection.completed",
        "etos.suite_runner.result_collection.delay",
        "etos.suite_runner.event_publish.attempts",
        "etos.suite_runner.event_publish.failures",
    }
    for metric_name, metric in exported.items():
        assert metric.data.data_points
        for point in metric.data.data_points:
            assert point.attributes["etos.api_version"] == api_version
            if ".event_publish." in metric_name:
                assert set(point.attributes) == {"etos.api_version", "etos.event_class"}
            else:
                assert set(point.attributes) == {
                    "etos.api_version",
                    "etos.stage",
                    "etos.outcome",
                }


@pytest.mark.parametrize("api_version", ["v0", "unknown", "private-version"])
def test_api_version_rejects_values_outside_the_bounded_vocabulary(metrics_reader, api_version):
    """Map unsupported versions to the single bounded unknown value."""
    metrics, reader = metrics_reader
    metrics.set_api_version(api_version)
    metrics.record_suite("Passed", 0.1)

    execution = _metrics_by_name(reader)["etos.suite_runner.suite.executions"]
    assert execution.data.data_points[0].attributes["etos.api_version"] == "unknown"


def test_dimensions_normalize_untrusted_values(metrics_reader):
    """Normalize unexpected input instead of exporting identifiers or raw event classes."""
    metrics, reader = metrics_reader
    metrics.record_suite("private-suite-id: raw failure detail", 0.1)
    metrics.record_result_collection("private-testrun-id", 0.2)
    metrics.record_event_publish_attempt("route/private-payload")

    exported = _metrics_by_name(reader)
    suite = exported["etos.suite_runner.suite.executions"].data.data_points[0]
    collection = exported["etos.suite_runner.result_collection.completed"].data.data_points[0]
    event = exported["etos.suite_runner.event_publish.attempts"].data.data_points[0]
    assert suite.attributes == {
        "etos.api_version": "unknown",
        "etos.stage": "suite_execution",
        "etos.outcome": "error",
    }
    assert collection.attributes == {
        "etos.api_version": "unknown",
        "etos.stage": "result_collection",
        "etos.outcome": "error",
    }
    assert event.attributes == {"etos.api_version": "unknown", "etos.event_class": "other"}


def test_shutdown_force_flushes_before_closing_meter_provider():
    """Always force-flush metrics before shutting down the exporter provider."""
    provider = Mock()
    with patch("etos_suite_runner.meter_provider", provider):
        shutdown_metrics()

    assert provider.method_calls == [call.force_flush(), call.shutdown()]


def test_shutdown_closes_meter_provider_when_force_flush_fails():
    """Shut down the exporter even when a final metrics flush raises."""
    provider = Mock()
    provider.force_flush.side_effect = RuntimeError("flush failed")
    with patch("etos_suite_runner.meter_provider", provider):
        with pytest.raises(RuntimeError, match="flush failed"):
            shutdown_metrics()

    provider.shutdown.assert_called_once()
