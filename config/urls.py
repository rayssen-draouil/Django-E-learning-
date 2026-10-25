"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path

from .views import home

urlpatterns = [
    path('', home, name='home'),
    path('users/', include('apps.users.urls')),
    path('', include('apps.courses.urls')),
]

if getattr(settings, 'DEBUG', False):
    from apps.courses.images import media_root, media_url
    urlpatterns += static(media_url(), document_root=media_root())

handler403 = "config.views.error_403"
handler404 = "config.views.error_404"
handler500 = "config.views.error_500"
