from django.urls import path, include
from .views import home

urlpatterns = [
    path('', home, name='home'),
    path('users/', include('apps.users.urls')),
    path('parcours/', include('apps.learning_paths.urls')),
    path('support/', include('apps.support.urls')),
]
