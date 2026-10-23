"""Local development settings; HTTP remains available."""
import os

from .base import *  # noqa: F401,F403
from .base import _env_bool, _env_list

DEBUG = _env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "development-only-secret-change-me")
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
