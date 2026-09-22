"""HTTP-шар. Тонкий навмисно.

В'юха робить три речі: валідує вхід серіалізатором, кличе сервіс,
перекладає доменну помилку в код відповіді. Ніякої логіки тут немає,
тому її нема де продублювати.
"""

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view
from rest_framework.response import Response

from . import services
from .models import Client, Event, Issue, Unit, WarrantyClaim
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


def domain_response(exc: services.DomainError) -> Response:
    """Порушене бізнес-правило це 409, а не 500.

    500 означає «ми зламались». Тут система працює правильно і свідомо
    відмовляє, тому конфлікт станів.
    """
    return Response(
        {"detail": str(exc), "code": type(exc).__name__},
        status=status.HTTP_409_CONFLICT,
    )


class ClientViewSet(viewsets.ModelViewSet):
    queryset = Client.objects.all()
    serializer_class = ClientSerializer


class UnitViewSet(viewsets.ModelViewSet):
    queryset = Unit.objects.all()
    serializer_class = UnitSerializer
    lookup_field = "ref"

    def get_queryset(self):
        qs = super().get_queryset()
        state = self.request.query_params.get("state")
        return qs.filter(state=state) if state else qs

    @action(detail=True, methods=["post"])
    def renew(self, request, ref=None):
        payload = RenewSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            renewal = services.renew_unit(
                unit_ref=ref,
                actor=request.user.username or "anonymous",
                **payload.validated_data,
            )
        except ObjectDoesNotExist:
            return Response(
                {"detail": "одиницю не знайдено"}, status=status.HTTP_404_NOT_FOUND
            )
        except services.DomainError as exc:
            return domain_response(exc)
        return Response(
            {"unit_ref": ref, "new_expires_at": renewal.new_expires_at},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"])
    def expiring(self, request):
        try:
            days = int(request.query_params.get("days", 14))
        except ValueError:
            return Response(
                {"detail": "days має бути числом"}, status=status.HTTP_400_BAD_REQUEST
            )
        units = services.expiring_units(days)
        return Response(UnitSerializer(units, many=True).data)


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
        try:
            issue = services.issue_unit(
                actor=request.user.username or "anonymous", **payload.validated_data
            )
        except ObjectDoesNotExist:
            return Response(
                {"detail": "одиницю або клієнта не знайдено"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except services.DomainError as exc:
            return domain_response(exc)
        return Response(IssueSerializer(issue).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def claim(self, request, pk=None):
        payload = ClaimCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            claim = services.open_claim(
                issue_id=int(pk),
                reason=payload.validated_data["reason"],
                actor=request.user.username or "anonymous",
            )
        except ObjectDoesNotExist:
            return Response(
                {"detail": "видачу не знайдено"}, status=status.HTTP_404_NOT_FOUND
            )
        except services.DomainError as exc:
            return domain_response(exc)
        return Response(
            WarrantyClaimSerializer(claim).data, status=status.HTTP_201_CREATED
        )


class ClaimViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = WarrantyClaim.objects.all()
    serializer_class = WarrantyClaimSerializer

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        payload = ClaimApproveSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            new_issue = services.approve_claim(
                claim_id=int(pk),
                replacement_ref=payload.validated_data["replacement_ref"],
                actor=request.user.username or "anonymous",
            )
        except ObjectDoesNotExist:
            return Response({"detail": "не знайдено"}, status=status.HTTP_404_NOT_FOUND)
        except services.DomainError as exc:
            return domain_response(exc)
        return Response(IssueSerializer(new_issue).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        try:
            claim = services.reject_claim(
                claim_id=int(pk), actor=request.user.username or "anonymous"
            )
        except ObjectDoesNotExist:
            return Response({"detail": "не знайдено"}, status=status.HTTP_404_NOT_FOUND)
        except services.DomainError as exc:
            return domain_response(exc)
        return Response(WarrantyClaimSerializer(claim).data)


class EventViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Журнал лише на читання. Історію не редагують."""

    queryset = Event.objects.all()
    serializer_class = EventSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        unit_ref = self.request.query_params.get("unit_ref")
        return qs.filter(unit__ref=unit_ref) if unit_ref else qs


@api_view(["GET"])
def healthz(request):
    return Response({"status": "ok"})
