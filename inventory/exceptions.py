"""Один обробник помилок замість try/except у кожній в'юсі.

До цього кожна дія мала власний блок на шість рядків, який ловив
``DomainError`` і ``ObjectDoesNotExist``. Це дублювання, і воно ламається
тихо: варто забути блок в одній новій дії, і порушене бізнес-правило
поїде користувачу як 500.

Тепер правило одне і живе в одному місці.
"""

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .services import DomainError


def exception_handler(exc: Exception, context: dict) -> Response | None:
    """Доменні помилки це відповідь, а не аварія.

    ``ProtectedError`` окремо: спроба видалити те, на що є посилання,
    без цього обробника летить назовні і стає 500. Для клієнта це виглядає
    як поломка сервера, хоча система відпрацювала правильно.
    """
    if isinstance(exc, DomainError):
        return Response(
            {"detail": str(exc), "code": type(exc).__name__},
            status=status.HTTP_409_CONFLICT,
        )

    if isinstance(exc, ProtectedError):
        return Response(
            {
                "detail": "об'єкт не можна видалити: на нього є посилання в історії",
                "code": "ProtectedError",
            },
            status=status.HTTP_409_CONFLICT,
        )

    if isinstance(exc, ObjectDoesNotExist):
        return Response(
            {"detail": "не знайдено", "code": "NotFound"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return drf_exception_handler(exc, context)
