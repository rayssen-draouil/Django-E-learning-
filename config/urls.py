from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from .views import home

urlpatterns = [
    path('', home, name='home'),
    path('users/', include('apps.users.urls')),
    path('parcours/', include('apps.learning_paths.urls')),
    path('support/', include('apps.support.urls')),
    path('', include('apps.courses.urls')),
]

if getattr(settings, 'DEBUG', False):
    from apps.courses.images import media_root, media_url
    urlpatterns += static(media_url(), document_root=media_root())

handler403 = "config.views.error_403"
handler404 = "config.views.error_404"
handler500 = "config.views.error_500"
