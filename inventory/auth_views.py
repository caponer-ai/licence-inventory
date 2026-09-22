"""Видача токена з обмеженням частоти.

Навіщо власна в'юха замість готової ``ObtainAuthToken``: у DRF вона
оголошена як ``throttle_classes = ()``, тобто лічильник запитів на ній
вимкнено. Для єдиного ендпоінта, куди можна стукати без токена і де
перевіряється пароль, це найгірше місце для «без обмежень»: перебір
виходить безкоштовним.

Окремий scope, а не загальний ``anon``: логін має бути жорсткішим за
звичайне читання, і його ліміт треба крутити незалежно.
"""

from drf_spectacular.utils import extend_schema
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.throttling import ScopedRateThrottle


class LoginThrottle(ScopedRateThrottle):
    scope = "login"


@extend_schema(
    summary="Отримати токен за логіном і паролем",
    description="Обмежено за частотою: підбір пароля коштує часу.",
)
class ThrottledObtainAuthToken(ObtainAuthToken):
    throttle_classes = [LoginThrottle]
    throttle_scope = "login"
