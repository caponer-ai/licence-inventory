"""HTTP-шар. Тонкий навмисно.

В'юха робить дві речі: валідує вхід серіалізатором і кличе сервіс. Помилки
перекладає в коди відповідей один обробник у ``inventory/exceptions.py``,
тому тут немає жодного try/except: їх неможливо забути в новій дії.
"""

from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import services
from .models import Client, Event, Issue, Unit, WarrantyClaim
from .permissions import IsAdminForDestroy
from .serializers import (
    ClaimApproveSerializer,
    ClaimCreateSerializer,
    ClientSerializer,
    EventSerializer,
    IssueCreateSerializer,
    IssueSerializer,
    RenewSerializer,
    UnitSerializer,
    WarrantyClaimSerializer,
)


def actor_of(request) -> str:
    """Хто зробив дію. Для анонімного запиту username порожній."""
    return getattr(request.user, "username", "") or "anonymous"


class ClientViewSet(viewsets.ModelViewSet):
    """Клієнти. Тут лежать контакти, тому доступ лише за токеном."""

    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [IsAdminForDestroy]


class UnitViewSet(viewsets.ModelViewSet):
    """Інвентар.

    ``state`` і ``expires_at`` тут тільки на читання. Змінити їх можна
    лише через дії (``renew``) або сервіс, бо кожна така зміна має лишити
    слід: запис у ``Renewal`` і подію в журналі. Інакше звичайний PUT
    переписав би строк дії, і пояснити клієнту нову дату було б нічим.
    """

    queryset = Unit.objects.all()
    serializer_class = UnitSerializer
    lookup_field = "ref"
    permission_classes = [IsAdminForDestroy]

    def get_queryset(self):
        qs = super().get_queryset()
        state = self.request.query_params.get("state")
        return qs.filter(state=state) if state else qs

    @action(detail=True, methods=["post"])
    def renew(self, request, ref=None):
        payload = RenewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        renewal = services.renew_unit(unit_ref=ref, actor=actor_of(request), **payload.validated_data)
        return Response(
            {
                "unit_ref": ref,
                "previous_expires_at": renewal.previous_expires_at,
                "new_expires_at": renewal.new_expires_at,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"])
    def expiring(self, request):
        """Пагінація така сама, як у звичайного списку.

        Різна форма відповіді на двох сусідніх ендпоінтах змушує клієнта
        писати дві гілки розбору, і рано чи пізно одну з них забувають.
        """
        try:
            days = int(request.query_params.get("days", 14))
        except ValueError:
            return Response(
                {"detail": "days має бути цілим числом", "code": "BadRequest"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if days < 0:
            return Response(
                {"detail": "days не може бути відʼємним", "code": "BadRequest"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        units = services.expiring_units(days)
        page = self.paginate_queryset(units)
        return self.get_paginated_response(UnitSerializer(page, many=True).data)


class IssueViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Issue.objects.select_related("unit", "client")
    serializer_class = IssueSerializer

    def create(self, request, *args, **kwargs):
        payload = IssueCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        issue = services.issue_unit(actor=actor_of(request), **payload.validated_data)
        return Response(IssueSerializer(issue).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        payload = ClaimCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        # get_object, а не int(pk): DRF пропускає в pk будь-що без
        # слеша і крапки, тому int("abc") летів ValueError і ставав 500.
        issue = self.get_object()
        claim = services.open_claim(
            issue_id=issue.pk,
            reason=payload.validated_data["reason"],
            actor=actor_of(request),
        )
        return Response(WarrantyClaimSerializer(claim).data, status=status.HTTP_201_CREATED)


class ClaimViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = WarrantyClaim.objects.select_related("issue__unit", "replacement_unit")
    serializer_class = WarrantyClaimSerializer

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        payload = ClaimApproveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        claim = self.get_object()
        new_issue = services.approve_claim(
            claim_id=claim.pk,
            replacement_ref=payload.validated_data["replacement_ref"],
            actor=actor_of(request),
        )
        return Response(IssueSerializer(new_issue).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        claim = services.reject_claim(claim_id=self.get_object().pk, actor=actor_of(request))
        return Response(WarrantyClaimSerializer(claim).data)


class EventViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Журнал лише на читання. Історію не редагують."""

    queryset = Event.objects.select_related("unit", "issue")
    serializer_class = EventSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        unit_ref = self.request.query_params.get("unit_ref")
        return qs.filter(unit__ref=unit_ref) if unit_ref else qs


@extend_schema(
    summary="Пульс сервісу",
    description="Перевіряє доступність бази. 503, якщо запит до неї не пройшов.",
    responses={200: None, 503: None},
)
@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([])
def healthz(request):
    """Пульс, який справді щось перевіряє.

    Ендпоінт, що завжди відповідає «ok», марний: балансувальник вважає
    інстанс живим, коли база вже недоступна. Тому робимо найдешевший
    можливий запит і віддаємо 503, якщо він не пройшов.

    Відкритий і без обмеження частоти свідомо: балансувальник не має
    токена і стукає сюди щосекунди. Даних звідси не витікає.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - назовні не пускаємо деталі
        return Response(
            {"status": "error", "database": type(exc).__name__},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({"status": "ok", "database": "ok"})
