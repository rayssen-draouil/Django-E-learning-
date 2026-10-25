"""Local development settings; HTTP remains available."""
import os

from .base import *  # noqa: F401,F403
from .base import _env_bool, _env_list

DEBUG = _env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "development-only-secret-change-me")
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

# Développement : relecture des templates à chaque requête (pas de cache),
# sinon toute modification de fichier .html n'apparaît qu'après un restart.
TEMPLATES[0]['APP_DIRS'] = False
TEMPLATES[0]['OPTIONS']['loaders'] = [
    "django.template.loaders.filesystem.Loader",
    "django.template.loaders.app_directories.Loader",
]

# Dev uniquement : inspection des pages authentifiées via le navigateur de
# diagnostic (session posable en JS). Défaut sécurisé : HttpOnly actif sauf si
# DJANGO_SESSION_HTTPONLY=0 est pose explicitement. Jamais en production.
SESSION_COOKIE_HTTPONLY = os.environ.get("DJANGO_SESSION_HTTPONLY", "1") != "0"
