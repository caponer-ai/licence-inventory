"""Token issuance with a rate limit.

Why a custom view instead of the ready-made ``ObtainAuthToken``: in DRF it
is declared with ``throttle_classes = ()``, so the request counter is
switched off. For the one endpoint that can be hit without a token and that
checks a password, "no limit" is the worst possible default: brute force
becomes free.

It gets its own throttle scope rather than sharing ``anon``, because login
should be stricter than ordinary reads and its limit must be tunable on its
own.

Honest limit: the counter lives in the configured cache. With the default
local-memory cache and several gunicorn workers each worker counts on its
own, so the effective limit is the configured rate times the number of
workers. A shared cache backend fixes that; this is an application-level
speed bump, not an authentication hardening mechanism.
"""

from drf_spectacular.utils import extend_schema
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.throttling import ScopedRateThrottle


@extend_schema(
    summary="Obtain a token with username and password",
    description="Rate limited: guessing a password costs time.",
)
class ThrottledObtainAuthToken(ObtainAuthToken):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"
