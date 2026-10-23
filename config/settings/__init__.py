"""Backward-compatible local settings module.

Deployments should select ``config.settings.production`` explicitly.
"""
from .development import *  # noqa: F401,F403
