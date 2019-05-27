import sys
import threading
import time
import traceback

import psutil
from flask import request

from flask_monitoringdashboard import config
from flask_monitoringdashboard.core.logger import log
from flask_monitoringdashboard.database import session_scope
from flask_monitoringdashboard.database.endpoint import update_last_accessed
from flask_monitoringdashboard.database.outlier import add_outlier
from flask_monitoringdashboard.database.request import get_avg_duration, add_request


class OutlierProfiler(threading.Thread):
    """
    Used for collecting additional information if the request is an outlier
    """

    def __init__(self, current_thread, endpoint, ip):
        threading.Thread.__init__(self)
        self._current_thread = current_thread
        self._endpoint = endpoint
        self._stopped = False
        self._ip = ip
        self._cpu_percent = None
        self._memory = None
        self._stacktrace = ''

        self._request = str(request.headers), str(request.environ), request.url.encode('utf-8')

    def run(self):
        # sleep for average * ODC ms
        with session_scope() as db_session:
            average = get_avg_duration(db_session, self._endpoint.id) * config.outlier_detection_constant
        time.sleep(average)
        if not self._stopped:
            stack_list = []
            try:
                frame = sys._current_frames()[self._current_thread]
            except KeyError:
                log('Can\'t get the stacktrace of the main thread.')
                return
            in_endpoint_code = False
            # filename, line number, function name, source code line
            for fn, ln, fun, line in traceback.extract_stack(frame):
                if self._endpoint.name == fun:
                    in_endpoint_code = True
                if in_endpoint_code:
                    stack_list.append('File: "{}", line {}, in "{}": "{}"'.format(fn, ln, fun, line))

            # Set the values in the object
            self._stacktrace = '<br />'.join(stack_list)
            self._cpu_percent = str(psutil.cpu_percent(interval=None, percpu=True))
            self._memory = str(psutil.virtual_memory())

    def stop(self, duration):
        self._stopped = True
        with session_scope() as db_session:
            update_last_accessed(db_session, endpoint_name=self._endpoint.name)
            request_id = add_request(db_session, duration=duration, endpoint_id=self._endpoint.id, ip=self._ip)
            if self._memory:
                add_outlier(db_session, request_id, self._cpu_percent, self._memory, self._stacktrace, self._request)

    def stop_by_profiler(self):
        self._stopped = True

    def add_outlier(self, request_id):
        if self._memory:
            with session_scope() as db_session:
                add_outlier(db_session, request_id, self._cpu_percent, self._memory, self._stacktrace, self._request)
