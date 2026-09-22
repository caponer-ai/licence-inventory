from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from inventory import views
from inventory.auth_views import ThrottledObtainAuthToken

router = DefaultRouter()
router.register("clients", views.ClientViewSet)
router.register("units", views.UnitViewSet)
router.register("issues", views.IssueViewSet)
router.register("claims", views.ClaimViewSet)
router.register("events", views.EventViewSet)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path("api/auth/token/", ThrottledObtainAuthToken.as_view(), name="token"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="docs",
    ),
    path("healthz/", views.healthz),
]
