from __future__ import annotations

import logging

from django.conf import settings


logger = logging.getLogger(__name__)


class GeminiError(Exception):
    """Base exception for safe, user-facing Gemini failures."""


class GeminiConfigurationError(GeminiError):
    pass


class GeminiGenerationError(GeminiError):
    pass


def generate_json(prompt: str, response_schema: dict) -> str:
    """Call Gemini and return its structured JSON text without exposing SDK errors."""
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        raise GeminiConfigurationError("The AI generator is not configured.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise GeminiConfigurationError("The Gemini client is not installed on this server.") from error

    try:
        options = types.HttpOptions(timeout=int(settings.GEMINI_TIMEOUT_SECONDS * 1000))
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=response_schema,
            temperature=0.7,
        )
        with genai.Client(api_key=api_key, http_options=options) as client:
            response = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config=config,
            )
        text = response.text
        if not isinstance(text, str) or not text.strip():
            raise GeminiGenerationError("Gemini returned no usable content.")
        return text
    except GeminiError:
        raise
    except Exception as error:
        status_code = getattr(error, "code", None)
        if not isinstance(status_code, int):
            status_code = "unknown"
        # Never log the prompt, response body, API key, or raw SDK message.
        logger.error(
            "Gemini request failed (exception=%s, status=%s)",
            type(error).__name__,
            status_code,
        )
        raise GeminiGenerationError("The Gemini service is temporarily unavailable.") from error
