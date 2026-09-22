"""One error handler instead of a try/except in every view.

Before this, each action carried its own six-line block catching
``DomainError`` and ``ObjectDoesNotExist``. That is duplication, and it
fails quietly: forget the block in one new action and a violated business
rule reaches the client as a 500.

Now the rule exists once, in one place.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .services import DomainError


def exception_handler(exc: Exception, context: dict) -> Response | None:
    """A domain error is an answer, not an outage.

    ``ProtectedError`` is handled separately: without this, an attempt to
    delete something that is still referenced escapes and becomes a 500.
    To the client that looks like a broken server, even though the system
    behaved exactly as designed.
    """
    if isinstance(exc, DomainError):
        return Response(
            {"detail": str(exc), "code": type(exc).__name__},
            status=status.HTTP_409_CONFLICT,
        )

    if isinstance(exc, ProtectedError):
        return Response(
            {
                "detail": "the object is referenced by history and cannot be deleted",
                "code": "ProtectedError",
            },
            status=status.HTTP_409_CONFLICT,
        )

    if isinstance(exc, ObjectDoesNotExist):
        return Response(
            {"detail": "not found", "code": "NotFound"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return drf_exception_handler(exc, context)
