from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings


logger = logging.getLogger(__name__)


class GeminiError(Exception):
    """Base exception for safe, user-facing AI generator failures."""


class GeminiConfigurationError(GeminiError):
    pass


class GeminiGenerationError(GeminiError):
    pass


def _generate_groq_json(prompt: str, response_schema: dict) -> str:
    api_key = getattr(settings, "GROQ_API_KEY", "")
    if not api_key:
        raise GeminiConfigurationError("The AI generator is not configured.")

    base_url = getattr(settings, "GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    timeout = getattr(settings, "GROQ_TIMEOUT", 35)
    candidates = [
        getattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b"),
        getattr(settings, "GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b"),
        "llama-3.3-70b-versatile",
    ]
    models = []
    for m in candidates:
        if m and m not in models:
            models.append(m)

    system_instruction = (
        "You are an expert learning assessment author. You MUST output ONLY a valid JSON object matching the requested schema. "
        "Do NOT return markdown code fences (like ```json), commentary, or notes outside the JSON object.\n"
        "Required schema structure:\n"
        "{\n"
        '  "title": string,\n'
        '  "description": string,\n'
        '  "questions": [\n'
        "    {\n"
        '      "type": "single_choice" | "multiple_choice" | "true_false",\n'
        '      "prompt": string,\n'
        '      "points": number,\n'
        '      "options": [string, ...],\n'
        '      "correct_option_indices": [integer, ...]\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    last_error: Exception | None = None
    for model in models:
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")

        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "SkillForge/1.0 (+Django)",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw_bytes = response.read()
                raw_text = raw_bytes.decode("utf-8") if isinstance(raw_bytes, bytes) else str(raw_bytes)
                res_data = json.loads(raw_text)

            content = ""
            if isinstance(res_data, dict):
                choices = res_data.get("choices")
                if choices and isinstance(choices, list):
                    first = choices[0]
                    if isinstance(first, dict):
                        msg = first.get("message")
                        if isinstance(msg, dict):
                            content = msg.get("content", "")
                        else:
                            content = first.get("text", "")
            if isinstance(content, str):
                content = content.strip()
                if content.startswith("```json"):
                    content = content[7:]
                elif content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()

            if not content:
                raise GeminiGenerationError("AI returned no usable content.")

            return content

        except urllib.error.HTTPError as error:
            last_error = error
            logger.warning("Groq HTTP error (%s) with model %s", error.code, model)
            if error.code in (401, 403):
                break
            continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            logger.warning("Groq network/parse error with model %s: %s", model, error)
            continue
        except GeminiGenerationError:
            raise
        except Exception as error:
            last_error = error
            logger.warning("Unexpected error calling Groq: %s", error)
            continue

    logger.error("All Groq model attempts failed. Last error: %s", type(last_error).__name__ if last_error else "None")
    raise GeminiGenerationError("The AI service is temporarily unavailable.") from last_error


def _generate_gemini_json(prompt: str, response_schema: dict) -> str:
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


def generate_json(prompt: str, response_schema: dict) -> str:
    """Generate structured JSON using Groq or Gemini."""
    groq_key = getattr(settings, "GROQ_API_KEY", "")
    if groq_key:
        try:
            return _generate_groq_json(prompt, response_schema)
        except GeminiConfigurationError:
            pass

    gemini_key = getattr(settings, "GEMINI_API_KEY", "")
    if gemini_key:
        return _generate_gemini_json(prompt, response_schema)

    raise GeminiConfigurationError("The AI generator is not configured.")
