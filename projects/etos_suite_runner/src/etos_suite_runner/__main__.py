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
import os
import json
import logging
import traceback

from etos_lib import ETOS

from .esr import ESR


LOGGER = logging.getLogger(__name__)


def main():
    """Entry point allowing external calls."""
    etos = ETOS("ETOS Suite Runner", os.getenv("SOURCE_HOST"), "ETOS Suite Runner")
    etos.config.set("results", [])
    esr = ESR(etos)
    try:
        esr.run()  # Blocking
        results = etos.config.get("results") or []
        result = None
        for suite_result in results:
            if suite_result.get("verdict") == "FAILED":
                result = suite_result
                break
            if suite_result.get("verdict") == "INCONCLUSIVE":
                result = suite_result
        if len(results) == 0:
            result = {
                "conclusion": "Inconclusive",
                "verdict": "Inconclusive",
                "description": "Got no results from ESR"
            }
        elif result is None:
            # No suite failed, so lets just pick the first result
            result = results[0]
        # Convert, for example, INCONCLUSIVE to Inconclusive to match the controller result struct
        # TODO Move the result struct to ETOS library and do this conversion on creation
        result["conclusion"] = f"{result['conclusion'][0].upper()}{result['conclusion'][1:].lower()}"
        result["verdict"] = f"{result['verdict'][0].upper()}{result['verdict'][1:].lower()}"
        with open("/dev/termination-log", "w", encoding="utf-8") as termination_log:
            json.dump(result, termination_log)
        LOGGER.info("ESR result: %r", result)
    except:
        result = {"conclusion": "Failed", "verdict": "Inconclusive", "description": traceback.format_exc()}
        with open("/dev/termination-log", "w", encoding="utf-8") as termination_log:
            json.dump(result, termination_log)
        raise
    finally:
        esr.etos.publisher.wait_for_unpublished_events()
        esr.etos.publisher.stop()
    LOGGER.info("ESR Finished Executing.", extra={"user_log": True})


def run():
    """Entry point for console_scripts."""
    main()


if __name__ == "__main__":
    run()
