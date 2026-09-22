"""Права доступу.

Один клас замість розсипу перевірок у в'юхах. Правило просте і його видно
цілком: читати й працювати можуть усі автентифіковані, видаляти лише
персонал.

Чому саме видалення окремо: усе інше в цьому сервісі оборотне або лишає
слід у журналі. Видалення не лишає нічого, тому це єдина дія, де ціна
помилки не покривається історією.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsAdminForDestroy(BasePermission):
    message = "видаляти може лише персонал"

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method == "DELETE":
            return bool(user.is_staff)
        return True


class ReadOnly(BasePermission):
    """Для журналу: історію читають, але не правлять."""

    def has_permission(self, request, view) -> bool:
        return request.method in SAFE_METHODS
