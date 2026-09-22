"""Access rules.

One class instead of scattered checks in views. The rule is simple and
visible in full: any authenticated user can read and act, only staff can
delete.

Why deletion is singled out: everything else here is either reversible or
leaves a trace in the audit log. A delete leaves nothing, so it is the one
action whose cost is not covered by history.
"""

from rest_framework.permissions import BasePermission


class IsAdminForDestroy(BasePermission):
    message = "only staff may delete"

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method == "DELETE":
            return bool(user.is_staff)
        return True
