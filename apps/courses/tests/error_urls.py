from django.core.exceptions import PermissionDenied
from django.urls import include, path


def boom(request):
    raise RuntimeError("Erreur de test 500")


def forbidden(request):
    raise PermissionDenied("Accès de test refusé")


urlpatterns = [
    path("__test__/boom/", boom),
    path("__test__/forbidden/", forbidden),
    path("", include("config.urls")),
]

handler403 = "config.views.error_403"
handler404 = "config.views.error_404"
handler500 = "config.views.error_500"
