from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_templated_email(
    *,
    subject: str,
    recipient_email: str,
    template_name_base: str,
    context: dict[str, Any],
) -> bool:
    """
    Safely render and dispatch an email with both HTML and plain-text alternatives.
    Catches delivery exceptions and logs them without breaking user transactions.
    """
    if not recipient_email or "@" not in recipient_email:
        logger.warning("Skipping email: invalid recipient '%s'", recipient_email)
        return False

    base_url = getattr(settings, "SITE_URL", "https://django-e-learning.onrender.com").rstrip("/")
    if "action_url" in context and context["action_url"].startswith("/"):
        context["action_url"] = f"{base_url}{context['action_url']}"

    try:
        html_template = f"emails/{template_name_base}.html"
        txt_template = f"emails/{template_name_base}.txt"

        html_content = render_to_string(html_template, context)
        try:
            txt_content = render_to_string(txt_template, context)
        except Exception:
            txt_content = html_content

        msg = EmailMultiAlternatives(
            subject=subject,
            body=txt_content,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "SkillForge <no-reply@skillforge.com>"),
            to=[recipient_email],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
        logger.info("Email '%s' sent successfully to %s", subject, recipient_email)
        return True
    except Exception as error:
        logger.error(
            "Failed to send email '%s' to %s: %s",
            subject,
            recipient_email,
            error,
        )
        return False
