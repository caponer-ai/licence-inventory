from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from inventory import views

router = DefaultRouter()
router.register("clients", views.ClientViewSet)
router.register("units", views.UnitViewSet)
router.register("issues", views.IssueViewSet)
router.register("claims", views.ClaimViewSet)
router.register("events", views.EventViewSet)

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(router.urls)),
    path("healthz/", views.healthz),
]
