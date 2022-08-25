# Copyright 2021 Axis Communications AB.
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
"""Log filter module will log only non-duplicate messages."""
from collections import deque


class DuplicateFilter:
    """Filter away duplicate log messages.

    Modified version of: https://stackoverflow.com/a/60462619/3323265
    """

    def __init__(self, logger):
        """Init filter with a max length.

        :param logger: Logger object to patch.
        :type logger: :obj:`logging.Logger`
        """
        self.msgs = deque(maxlen=100)
        self.logger = logger

    def filter(self, record):
        """Filter away duplicate messages.

        :param record: Log record.
        :type record: :obj:`logging.LogRecord`
        :return: Whether or not to filter.
        :rtype: bool
        """
        msg = f"{record.msg}{record.args}"
        is_duplicate = msg in self.msgs
        if not is_duplicate:
            self.msgs.append(msg)
        return not is_duplicate

    def __enter__(self):
        """Context manager to patch an existing logger."""
        self.logger.addFilter(self)

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager. Remove the filter."""
        self.logger.removeFilter(self)
