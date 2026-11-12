from __future__ import annotations

import collections
import datetime
import logging
import queue
import threading
from typing import Any, Deque, Dict, List, Tuple

from ..logger import logger

LOG_BUFFER_SIZE = 200


class EventHub:
    """Fan-out of `log` and `status` events to SSE subscribers with a ring buffer of last log lines."""
    def __init__(self, buffer_size: int = LOG_BUFFER_SIZE) -> None:
        self._lock = threading.Lock()
        self._log: Deque[Dict[str, Any]] = collections.deque(maxlen=buffer_size)
        self._subscribers: List[queue.Queue] = []

    def subscribe(self) -> Tuple[queue.Queue, List[Dict[str, Any]]]:
        """Register a subscriber. Returns its queue and the snapshot of buffered log lines.
        Registration and snapshot are atomic: no line is lost or duplicated."""
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
            return q, list(self._log)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish_log(self, level: str, message: str) -> None:
        entry = {'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'level': level, 'message': message}
        with self._lock:
            self._log.append(entry)
            for q in self._subscribers:
                q.put(('log', entry))

    def publish_status(self, status: Dict[str, Any]) -> None:
        with self._lock:
            for q in self._subscribers:
                q.put(('status', status))

    def log_lines(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._log)


class HubLogHandler(logging.Handler):
    """Logging handler that forwards records of the application logger to an `EventHub`."""
    def __init__(self, hub: EventHub) -> None:
        super().__init__()
        self._hub = hub
        self.setFormatter(logging.Formatter('%(message)s'))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._hub.publish_log(record.levelname, self.format(record))
        except Exception:
            self.handleError(record)


def attach_log_handler(hub: EventHub) -> Tuple[HubLogHandler, int]:
    """Attach handler to the application logger and make sure INFO records pass.
    Returns the handler and the previous logger level (for `detach_log_handler`)."""
    handler = HubLogHandler(hub)
    previous_level = logger.level
    logger.addHandler(handler)
    if logger.getEffectiveLevel() > logging.INFO:
        logger.setLevel(logging.INFO)
    return handler, previous_level


def detach_log_handler(handler: HubLogHandler, previous_level: int) -> None:
    logger.removeHandler(handler)
    logger.setLevel(previous_level)
