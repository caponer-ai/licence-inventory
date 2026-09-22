"""A request id that every log line carries.

Two requests interleaved in one log file cannot be told apart without it,
and that is exactly the situation where the log is needed. The id is taken
from an inbound ``X-Request-ID`` when a proxy sets one, so a trace can be
followed across services instead of restarting at this one.

Kept as a context variable rather than passed around: logging happens deep
inside code that has no reason to know about HTTP.
"""

import logging
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(value: str) -> str:
    _request_id.set(value)
    return value


def get_request_id() -> str:
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class RequestIdMiddleware:
    """Assign an id to every request and hand it back in the response.

    Echoing it in the response header matters: when a client reports a
    failure, the id they can read off their own side is the one that finds
    the server's side of the story.
    """

    HEADER = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(self.HEADER, "")
        request_id = set_request_id(incoming or uuid.uuid4().hex)
        response = self.get_response(request)
        response[self.HEADER] = request_id
        return response
