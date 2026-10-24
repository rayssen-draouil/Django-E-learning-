from __future__ import annotations

import json
import math
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone

from django.conf import settings
from pymongo.errors import PyMongoError

from config.mongodb import db

ai_logs_collection = db["ai_logs"]


class AIError(Exception):
    pass


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(cleaned)
    except (TypeError, ValueError) as error:
        raise AIError("La réponse de l'IA est invalide.") from error


def generate_course_content(title: str, level: str) -> dict:
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise AIError("La clé ANTHROPIC_API_KEY n'est pas configurée.")

    prompt = (
        f"Rédige en français une description de cours (120 mots maximum) et exactement "
        f"5 objectifs d'apprentissage pour le titre « {title} » de niveau {level}. "
        'Réponds uniquement en JSON: {"description": "...", "objectives": ["...", ...]}.'
    )
    payload = json.dumps({
        "model": settings.AI_MODEL,
        "max_tokens": 900,
        "temperature": 0.4,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=settings.AI_TIMEOUT) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise AIError("Limite de requêtes IA atteinte. Réessayez dans un instant.") from error
        if error.code in (401, 403):
            raise AIError("Accès à l'API IA refusé. Vérifiez la clé.") from error
        raise AIError("L'API IA a renvoyé une erreur.") from error
    except (socket.timeout, TimeoutError) as error:
        raise AIError("L'API IA ne répond pas. Réessayez plus tard.") from error
    except (urllib.error.URLError, OSError) as error:
        raise AIError("Impossible de contacter l'API IA.") from error

    try:
        text = "".join(
            block.get("text", "") for block in body["content"] if block.get("type") == "text"
        )
        content = _extract_json(text)
        description = str(content.get("description", "")).strip()[:5000]
        objectives = [
            str(item).strip() for item in content.get("objectives", []) if str(item).strip()
        ]
    except (KeyError, TypeError, AttributeError) as error:
        raise AIError("La réponse de l'IA est invalide.") from error
    if not description or len(objectives) != 5:
        raise AIError("La réponse de l'IA est incomplète.")
    return {"description": description, "objectives": objectives}


def record_ai_log(*, duration_ms: int, success: bool, output_length: int = 0, error: str = "") -> None:
    try:
        ai_logs_collection.insert_one({
            "created_at": datetime.now(timezone.utc),
            "duration_ms": duration_ms,
            "success": success,
            "output_length": output_length,
            "error": error[:300],
        })
    except PyMongoError:
        pass


def get_ai_stats() -> dict:
    documents = list(ai_logs_collection.find({}, {"duration_ms": 1, "success": 1}))
    durations = sorted(document.get("duration_ms", 0) for document in documents)
    count = len(documents)
    failures = sum(not document.get("success") for document in documents)
    p95 = durations[max(0, math.ceil(count * 0.95) - 1)] if durations else 0
    return {
        "count": count,
        "average_ms": round(sum(durations) / count, 1) if count else 0,
        "p95_ms": p95,
        "failure_rate": round(failures / count * 100, 1) if count else 0,
    }
