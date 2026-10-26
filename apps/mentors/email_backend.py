from __future__ import annotations

import json
import logging
import re
import ssl
import urllib.request
from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend
from django.utils.functional import cached_property

logger = logging.getLogger(__name__)


class BrevoEmailBackend(EmailBackend):
    """
    Email Backend supporting both Brevo HTTP API (port 443 - works on Render Free tier)
    and standard SMTP (port 587 - works on paid servers/local).
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("timeout", getattr(settings, "EMAIL_TIMEOUT", 10))
        super().__init__(*args, **kwargs)

    @cached_property
    def ssl_context(self):
        ctx = super().ssl_context
        ssl_verify = getattr(settings, "EMAIL_SSL_VERIFY", True)
        if not ssl_verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def send_messages(self, email_messages):
        api_key = getattr(settings, "BREVO_API_KEY", "") or getattr(settings, "brevo_api_key", "")
        # If Brevo API key is available, use HTTPS API (port 443) which Render never blocks
        if api_key and (api_key.startswith("xkeysib-") or len(api_key) > 30):
            sent_count = 0
            for msg in email_messages:
                try:
                    if self._send_via_http(msg, api_key):
                        sent_count += 1
                except Exception as e:
                    logger.error("Brevo HTTP API delivery failed for '%s': %s", msg.subject, e)
                    if not self.fail_silently:
                        raise
            return sent_count

        # Otherwise fall back to standard SMTP
        try:
            return super().send_messages(email_messages)
        except Exception as e:
            logger.error("SMTP delivery failed: %s (Render Free tier blocks SMTP ports 25, 465, 587)", e)
            if not self.fail_silently:
                raise
            return 0

    def _send_via_http(self, message, api_key: str) -> bool:
        import urllib.error

        from_str = message.from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "miladiadem58@gmail.com")
        m = re.match(r'^(?:"?([^"]*)"?\s)?<?([^>]+)>?$', str(from_str))
        if m and m.group(2):
            sender_name = m.group(1) or "SkillForge"
            sender_email = m.group(2).strip()
        else:
            sender_name = "SkillForge"
            sender_email = str(from_str).strip()

        if not sender_email or "@" not in sender_email:
            sender_email = "miladiadem58@gmail.com"

        html_content = ""
        text_content = message.body or ""
        for content, mimetype in getattr(message, "alternatives", []):
            if mimetype == "text/html":
                html_content = content
                break
        if not html_content and getattr(message, "content_subtype", "plain") == "html":
            html_content = message.body

        payload = {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"email": to_addr} for to_addr in message.to],
            "subject": message.subject,
        }
        if html_content:
            payload["htmlContent"] = html_content
        if text_content:
            payload["textContent"] = text_content

        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "accept": "application/json",
                "api-key": api_key.strip(),
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=getattr(settings, "EMAIL_TIMEOUT", 10)) as resp:
                logger.info("Brevo HTTPS API delivery successful: %s for %s", resp.status, message.to)
                return resp.status in (200, 201, 202)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.error("Brevo API HTTP %d error sending to %s: %s", e.code, message.to, err_body)
            if not self.fail_silently:
                raise
            return False
        except Exception as e:
            logger.error("Brevo API network error sending to %s: %s", message.to, e)
            if not self.fail_silently:
                raise
            return False
