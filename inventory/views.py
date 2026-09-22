"""The HTTP layer. Thin on purpose.

A view does two things: validate the input with a serializer and call the
service. Errors are translated into status codes by a single handler in
``inventory/exceptions.py``, so there is not one try/except here and no way
to forget one in a new action.
"""

from django.db import connection
from django.db.models import QuerySet
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
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
    """Who performed the action. Anonymous requests carry an empty username."""
    return getattr(request.user, "username", "") or "anonymous"


class ClientViewSet(viewsets.ModelViewSet):
    """Clients. Contact details live here, so a token is required."""

    queryset = Client.objects.all()
    serializer_class = ClientSerializer
    permission_classes = [IsAdminForDestroy]


class UnitViewSet(viewsets.ModelViewSet):
    """Inventory.

    ``state`` and ``expires_at`` are read-only here. They change only
    through actions (``renew``) or the service layer, because every such
    change has to leave a trace: a Renewal row and an audit log entry.
    Otherwise a plain PUT would rewrite the expiry and there would be
    nothing left to explain the new date to the client.
    """

    queryset = Unit.objects.all()
    serializer_class = UnitSerializer
    lookup_field = "ref"
    permission_classes = [IsAdminForDestroy]

    def get_queryset(self) -> QuerySet:
        qs = super().get_queryset()
        state = self.request.query_params.get("state")
        return qs.filter(state=state) if state else qs

    @action(detail=True, methods=["post"])
    def renew(self, request: Request, ref: str | None = None) -> Response:
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
    def expiring(self, request: Request) -> Response:
        """Same pagination envelope as the plain list.

        A different response shape on two neighbouring endpoints forces the
        client to keep two parsing branches, and sooner or later one of them
        is forgotten.
        """
        try:
            days = int(request.query_params.get("days", 14))
        except ValueError:
            return Response(
                {"detail": "days must be an integer", "code": "BadRequest"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if days < 0:
            return Response(
                {"detail": "days cannot be negative", "code": "BadRequest"},
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

    def create(self, request: Request, *args: object, **kwargs: object) -> Response:
        payload = IssueCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        issue = services.issue_unit(actor=actor_of(request), **payload.validated_data)
        return Response(IssueSerializer(issue).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def claim(self, request: Request, pk: str | None = None) -> Response:
        payload = ClaimCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        # get_object instead of int(pk): DRF accepts anything without a
        # slash or a dot as pk, so int("abc") raised ValueError and became
        # a 500 instead of an honest 404.
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
    def approve(self, request: Request, pk: str | None = None) -> Response:
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
    def reject(self, request: Request, pk: str | None = None) -> Response:
        claim = services.reject_claim(claim_id=self.get_object().pk, actor=actor_of(request))
        return Response(WarrantyClaimSerializer(claim).data)


class EventViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The audit log is read-only. History does not get edited."""

    queryset = Event.objects.select_related("unit", "issue")
    serializer_class = EventSerializer

    def get_queryset(self) -> QuerySet:
        qs = super().get_queryset()
        unit_ref = self.request.query_params.get("unit_ref")
        return qs.filter(unit__ref=unit_ref) if unit_ref else qs


@extend_schema(
    summary="Service heartbeat",
    description="Checks that the database answers. Returns 503 if it does not.",
    responses={200: None, 503: None},
)
@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([])
def healthz(request: Request) -> Response:
    """A heartbeat that actually checks something.

    An endpoint that always answers "ok" is useless: the load balancer keeps
    the instance in rotation while the database is already gone. So we run
    the cheapest possible query and return 503 if it did not go through.

    Open and unthrottled on purpose: the load balancer has no token and hits
    this every second, and no data leaks from here.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - details never leave the server
        return Response(
            {"status": "error", "database": type(exc).__name__},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({"status": "ok", "database": "ok"})
