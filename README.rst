=================
ETOS Suite Runner
=================

.. image:: https://img.shields.io/badge/Stage-Sandbox-yellow.svg
  :target: https://github.com/eiffel-community/community/blob/master/PROJECT_LIFECYCLE.md#stage-sandbox

ETOS (Eiffel Test Orchestration System) Suite Runner


OpenTelemetry metrics
=====================

When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is configured, the Suite Runner exports metrics over OTLP
using the same resource as its traces. The resource includes ``etos.version`` from
``ETOS_VERSION`` (or ``unknown`` when unset); the value is not a metric attribute. Metrics are
force-flushed and the meter provider is shut down before the short-lived runner exits.

The metrics below are emitted for controller-managed runs (``IDENTIFIER`` is set), whose Suite
Runner waits for Test Runner results and never starts the test payload. Each metric includes the
bounded ``etos.api_version`` attribute (``v1alpha1``, ``v1beta1`` or ``unknown``), read from the
TestRun's ``spec.schemaVersion``. The ``etos.stage`` and ``etos.outcome`` keys classify suite and
result-collection metrics, while ``etos.event_class`` classifies publish metrics.

- ``etos.suite_runner.suite.executions`` is a ``{execution}`` counter with one terminal outcome per
  process execution; ``etos.suite_runner.suite.duration`` is a seconds histogram over that process
  lifetime. ``etos.stage`` is ``suite_execution``; ``etos.outcome`` is ``success``, ``failure``,
  ``inconclusive`` or ``error``.
- ``etos.suite_runner.result_collection.completed`` is a ``{collection}`` counter and
  ``etos.suite_runner.result_collection.delay`` is a seconds histogram, each recorded once per
  sub-suite when its wait ends. ``etos.stage`` is ``result_collection``; ``etos.outcome`` is
  ``received``, ``missing`` or ``error``. A received failing or inconclusive test result is still
  ``received``;
  the suite outcome records the test result separately.
- ``etos.suite_runner.event_publish.attempts`` is a ``{attempt}`` counter and
  ``etos.suite_runner.event_publish.failures`` is a ``{failure}`` counter. They count each call into
  an ETOS event publisher and each call that raises, respectively. Event classes are bounded to
  ``activity_started``, ``recipe_collection_created``, ``activity_triggered``,
  ``activity_finished``, ``activity_canceled``, ``test_suite_started``, ``test_suite_finished`` and
  ``shutdown`` (as ``etos.event_class``).

The Suite Runner does not retry event-publisher calls. Retries or redeliveries performed inside
``etos_lib`` are not observable at this boundary and therefore have no redelivery metric; absence of
such a metric must not be interpreted as zero library-level redeliveries. No metric includes
TestRun/suite identifiers, routing keys, event payloads or raw errors.

Metrics are enabled for both v1alpha1 and v1beta1 controller-managed runs; ``etos.api_version``
distinguishes them. Standalone v0 runs do not emit metrics.


Description
===========

The ETOS Suite Runner is responsible for making sure that all test suites started in ETOS are executed to completion.
Note that the ESR does not do the actual execution of test suites, it merely tells ETOS Test Runner to execute within an execution space.


Installation
============

   pip install .


Contribute
==========

- Issue Tracker: https://github.com/eiffel-community/etos/issues
- Source Code: https://github.com/eiffel-community/etos-suite-runner


Support
=======

If you are having issues, please let us know.
There is a mailing list at: etos-maintainers@googlegroups.com or just write an Issue.
