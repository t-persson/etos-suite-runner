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
"""ETOS suite runner log listener and webserver."""
import logging
import pathlib
import signal
import sys
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse

from .listener import Listener

LOGGER = logging.getLogger(__name__)
logging.getLogger("pika").setLevel(logging.WARNING)

APP = FastAPI()
LOCK = threading.Lock()

# TODO: Temporarily using two files for this. Should be changed to only be EVENT_FILE in the future
# when the old endpoint is being removed.
LOG_FILE = pathlib.Path("./log")
LOG_FILE.touch()
EVENT_FILE = pathlib.Path("./events")
EVENT_FILE.touch()


@APP.get("/log")
async def logs():
    """Endpoint serving a log file."""
    with LOCK:
        return FileResponse(LOG_FILE)


@APP.get("/v1/log", response_class=PlainTextResponse)
async def v1logs(start: int, end: int = -1):
    """Endpoint serving events from ETOS."""
    with LOCK:
        text = EVENT_FILE.read_text(encoding="UTF-8")
    start = start - 1
    if end == -1:  # not set
        log_lines = text.splitlines()[start:]
    else:
        log_lines = text.splitlines()[start:end]
    return "\n".join(log_lines)


def main():
    """Entry point allowing external calls."""
    listener = Listener(LOCK, LOG_FILE, EVENT_FILE)

    @APP.on_event("shutdown")
    def stop(*_):
        """Stop the listener."""
        if listener.is_alive():
            LOCK.acquire(timeout=300)  # pylint: disable=consider-using-with
            listener.clear()
            listener.stop()
            listener.join()

    signal.signal(signal.SIGTERM, stop)
    logformat = "[%(asctime)s] %(levelname)s:%(name)s:%(message)s"
    logging.basicConfig(
        level=logging.DEBUG,
        stream=sys.stdout,
        format=logformat,
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    listener.start()

    try:
        uvicorn.run(APP, host="0.0.0.0", port=8000)
    finally:
        stop()


def run():
    """Entry point for console_scripts."""
    main()


if __name__ == "__main__":
    run()
