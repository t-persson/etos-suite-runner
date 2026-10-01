#!/usr/bin/env python
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
# -*- coding: utf-8 -*-
"""ETOS suite runner module."""

import json
import logging
import os
import time
import traceback

from etos_lib import ETOS

from . import METRICS, shutdown_metrics
from .esr import ESR

LOGGER = logging.getLogger(__name__)


def main():
    """Entry point allowing external calls."""
    esr = None
    outcome = "error"
    started_at = time.monotonic()
    metrics_enabled = os.getenv("IDENTIFIER") is not None
    try:
        etos = ETOS("ETOS Suite Runner", os.getenv("SOURCE_HOST"), "ETOS Suite Runner")
        esr = ESR(etos)
        result = esr.run()  # Blocking
        with open("/dev/termination-log", "w", encoding="utf-8") as termination_log:
            json.dump(result.model_dump(), termination_log)
        LOGGER.info("ESR result: %r", result)
        outcome = result.verdict
    except:
        result = {
            "conclusion": "Failed",
            "verdict": "Inconclusive",
            "description": traceback.format_exc(),
        }
        with open("/dev/termination-log", "w", encoding="utf-8") as termination_log:
            json.dump(result, termination_log)
        raise
    finally:
        try:
            if esr is not None:
                esr.etos.publisher.wait_for_unpublished_events()
                esr.etos.publisher.stop()
        except BaseException:
            outcome = "error"
            raise
        finally:
            try:
                if metrics_enabled:
                    METRICS.record_suite(outcome, time.monotonic() - started_at)
            finally:
                shutdown_metrics()
    LOGGER.info("ESR Finished Executing.")


def run():
    """Entry point for console_scripts."""
    main()


if __name__ == "__main__":
    run()
