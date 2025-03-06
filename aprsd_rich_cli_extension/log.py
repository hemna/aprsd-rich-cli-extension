import logging
import queue

textual_log_queue = queue.Queue(maxsize=200)


class TextualLogHandler(logging.Handler):
    """Capture log messages and send them to the log queue."""

    def __init__(self) -> None:
        logging.Handler.__init__(self=self)

    def emit(self, record) -> None:
        textual_log_queue.put(record)
