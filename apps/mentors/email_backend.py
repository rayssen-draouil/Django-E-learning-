from __future__ import annotations

import ssl
from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend
from django.utils.functional import cached_property


class BrevoEmailBackend(EmailBackend):
    """
    SMTP Email Backend supporting Brevo SMTP and optional SSL verification override.
    """

    @cached_property
    def ssl_context(self):
        ctx = super().ssl_context
        ssl_verify = getattr(settings, "EMAIL_SSL_VERIFY", True)
        if not ssl_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx
