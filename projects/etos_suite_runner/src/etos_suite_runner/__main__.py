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
import logging
import traceback

from .esr import ESR

LOGGER = logging.getLogger(__name__)


def main():
    """Entry point allowing external calls."""
    esr = ESR()
    try:
        esr.run()  # Blocking
    except:
        with open("/dev/termination-log", "w", encoding="utf-8") as termination_log:
            termination_log.write(traceback.format_exc())
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
