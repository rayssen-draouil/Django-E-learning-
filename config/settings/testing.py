"""Réglages pour l'exécution de la suite de tests.

Exécuter avec :
    python manage.py test --settings=config.settings.testing

Garanties :
- les données vont dans la base ``e_learning_test`` : la vraie base
  ``e_learning`` n'est jamais touchée ;
- aucun e-mail réel n'est envoyé (le relais Brevo est désactivé) ;
- hachage rapide (MD5) pour des tests courts ;
- DEBUG=False : les pages d'erreur 404/500 réelles sont testées, la CSRF active.
"""
import os

from .base import *  # noqa: F401,F403
from .base import _env_list

# Base de données séparée pour les tests (config/mongodb.py la lit au chargement).
MONGODB_DATABASE = "e_learning_test"

DEBUG = False
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")

# Pas d'envoi réel : le mode développement « lien direct » s'active
# via override_settings(DEBUG=True) dans les tests concernés.
EMAIL_HOST_USER = ""

# Hachage rapide uniquement pour les tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Aucun appel réel à l'IA pendant les tests : la clé est vidée, ce qui force
# le repli heuristique local (aucun réseau).
GROQ_API_KEY = ""
