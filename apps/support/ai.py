"""AI assistant for the Support module (Groq — OpenAI-compatible API).

Each requirement is covered:
  1. automatic classification (category + priority);
  2. solution suggestions from the FAQ;
  3. automatic ticket summary for the administrator;
  4. suggested reply generation — never sent without human validation.

All functions are defensive: if the AI API is unavailable
(missing key, network down, quota exhausted), they return a "heuristic"
response built with the module's local rules. Support is therefore
never blocked by the AI.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from urllib.error import URLError

from django.conf import settings

from . import services

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SOURCE_AI = "groq"
SOURCE_LOCAL = "local assistant"

VALID_CATEGORIES = {key for key, _ in services.CATEGORIES}
VALID_PRIORITIES = {key for key, _ in services.PRIORITIES}

# Response displayed if AI and heuristics have nothing to propose.
NO_SOLUTION = "The support team will reply here as soon as possible."

SYSTEM_RULES = (
    "You are the AI support assistant for the SkillForge e-learning platform. "
    "Always reply exclusively in English and ONLY with valid JSON, with no surrounding text. "
    "Be concise and practical."
)


class AIServiceError(Exception):
    """The request to Groq failed (missing key, network, format)."""


def _chat_content(messages: list[dict]) -> str:
    """Groq call; returns the response text (reasoning excluded)."""
    api_key = getattr(settings, "GROQ_API_KEY", "")
    if not api_key:
        raise AIServiceError("Groq API key missing (GROQ_API_KEY).")
    payloads = [settings.GROQ_MODEL, settings.GROQ_FALLBACK_MODEL]
    erreur: Exception | None = None
    for model in payloads:
        request = urllib.request.Request(
            GROQ_URL,
            data=json.dumps({
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "max_completion_tokens": getattr(settings, "GROQ_MAX_TOKENS", 900),
            }).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                     "User-Agent": "SkillForge-Support/1.0 (+Django)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=getattr(settings, "GROQ_TIMEOUT", 25)) as response:
                data = json.loads(response.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            if content:
                return content
        except Exception as error:  # noqa: BLE001 — any network failure triggers fallback
            err = error
            logger.warning("Groq unavailable (%s): trying next model or local fallback.", error)
    raise AIServiceError(f"Groq call impossible: {err}")


def _extract_json(content: str) -> dict | None:
    """Extracts the first JSON object from a response (tolerant to fluff)."""
    content = (content or "").strip()
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(content[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _chat_json(system: str, user: str) -> dict | None:
    """Groq request with JSON output; returns None if AI doesn't respond."""
    try:
        content = _chat_content([{"role": "system", "content": system},
                                 {"role": "user", "content": user}])
    except AIServiceError as error:
        logger.warning("AI assistant unavailable: %s", error)
        return None
    return _extract_json(content)


# ---------------------------------------------------------------- 1. classification

def _priority_local(text: str) -> str:
    """Urgency heuristic: normal by default, urgent/low on strong signals."""
    text = text.casefold()
    urgency = ("urgent", "impossible", "blocked", "block", "immediately",
               "nothing works", "exam", "deadline", "crash")
    low = ("question", "how to", "suggestion", "information request")
    if any(word in text for word in urgency):
        return "urgent"
    if any(word in text for word in low):
        return "low"
    return "normal"


def ai_classify(title: str, description: str) -> dict:
    """1) Automatic classification: suggested category + priority."""
    text = f"{title} {description}".strip()
    fallback = {
        "source": SOURCE_LOCAL,
        "model": None,
        "category": services.category_suggestion(services.normalize_text(text)),
        "priority": _priority_local(text.casefold()),
        "explanation": "Keyword-based classification (AI assistant unavailable).",
    }
    response = _chat_json(
        SYSTEM_RULES,
        json.dumps({
            "task": "Classify this e-learning support ticket",
            "ticket": {"title": title, "description": description},
            "allowed_categories": sorted(VALID_CATEGORIES),
            "allowed_priorities": sorted(VALID_PRIORITIES),
            "response_format": {"category": "…", "priority": "…",
                                "explanation": "one short sentence in English"},
        }, ensure_ascii=False),
    )
    if not response or response.get("category") not in VALID_CATEGORIES \
            or response.get("priority") not in VALID_PRIORITIES:
        return fallback
    return {
        "source": SOURCE_AI,
        "model": settings.GROQ_MODEL,
        "category": response["category"],
        "priority": response["priority"],
        "explanation": str(response.get("explanation") or fallback["explanation"]),
    }


# ------------------------------------------------------- 2. FAQ solutions

def ai_suggestions(title: str, description: str) -> dict:
    """2) Solution suggestions: FAQ search + AI ranking."""
    text = f"{title} {description}".strip().casefold()
    candidates = services.search_faq(text, limit=6)[:6]
    fallback = {
        "source": SOURCE_LOCAL,
        "model": None,
        "classification": {
            "category": services.category_suggestion(services.normalize_text(text)),
            "priority": _priority_local(text),
            "explanation": "Suggestions from the knowledge base (keyword match).",
        },
        "solutions": [
            {"question": entry.get("question", ""), "answer": entry.get("answer", NO_SOLUTION)}
            for entry in candidates[:3]
        ] or [{"question": "No automatic suggestions", "answer": NO_SOLUTION}],
    }
    response = _chat_json(
        SYSTEM_RULES,
        json.dumps({
            "task": "Classify the ticket and choose up to 3 relevant solutions",
            "ticket": {"title": title, "description": description},
            "faq_disponible": [
                {"id": index, "question": entry.get("question", ""), "answer": entry.get("answer", "")}
                for index, entry in enumerate(candidates)
            ],
            "response_format": {
                "category": "courses|quizzes|mentoring|account|other",
                "priority": "low|normal|high|urgent",
                "explanation": "one short sentence in English",
                "faq_ids": "[0, 1]",
            },
        }, ensure_ascii=False),
    )
    if not response:
        return fallback
    ids = response.get("faq_ids") or []
    if not isinstance(ids, list):
        ids = []
    solutions = []
    for identifier in ids[:3]:
        try:
            entry = candidates[int(identifier)]
        except (TypeError, ValueError, IndexError):
            continue
        solutions.append({"question": entry.get("question", ""), "answer": entry.get("answer", NO_SOLUTION)})
    if not solutions:
        solutions = fallback["solutions"]
    classification = ai_classify(title, description)
    return {
        "source": SOURCE_AI,
        "model": settings.GROQ_MODEL,
        "classification": classification,
        "solutions": solutions,
    }


# ----------------------------------------------------------------- 3. AI summary

def _transcript(ticket: dict, ticket_messages: list[dict], notes: list[dict],
                limit: int = 6000) -> str:
    """Structures the ticket content into a compact discussion thread."""
    thread = [f"STUDENT (« {ticket.get('title', '')} ») : {ticket.get('description', '')}"]
    for message in ticket_messages:
        role = "SUPPORT" if message.get("sender_role") == "admin" else "STUDENT"
        thread.append(f"{role} : {message.get('body', '')}")
    for note in notes:
        thread.append(f"INTERNAL NOTE (team) : {note.get('body', '')}")
    return "\n".join(thread)[:limit]


def ai_summary(ticket: dict, ticket_messages: list[dict], notes: list[dict]) -> dict:
    """3) Automatic summary: main problem + actions already taken."""
    description = " ".join(str(ticket.get("description", "")).split())
    fallback = {
        "source": SOURCE_LOCAL,
        "model": None,
        "summary": description[:280],
        "problem": ticket.get("title", ""),
        "actions_done": [
            f"{len(ticket_messages)} message(s) exchanged" if ticket_messages
            else "No support reply yet",
        ],
    }
    response = _chat_json(
        SYSTEM_RULES,
        json.dumps({
            "task": "Summarize this ticket for the support team (help them handle it quickly)",
            "ticket": {
                "title": ticket.get("title", ""),
                "category": ticket.get("category", ""),
                "status": ticket.get("status", ""),
                "priority": ticket.get("priority", ""),
            },
            "conversation": _transcript(ticket, ticket_messages, notes),
            "format_reponse": {
                "summary": "2 to 3 sentences maximum",
                "problem": "the main issue in one sentence",
                "actions_done": "list what has already been tried or checked",
            },
        }, ensure_ascii=False),
    )
    if not response or not str(response.get("summary") or "").strip():
        return fallback
    actions = response.get("actions_done") or []
    if not isinstance(actions, list):
        actions = [str(actions)]
    return {
        "source": SOURCE_AI,
        "model": settings.GROQ_MODEL,
        "summary": str(response.get("summary", fallback["summary"])).strip(),
        "problem": str(response.get("problem", fallback["problem"])).strip(),
        "actions_done": [str(item).strip() for item in actions if str(item).strip()],
    }


# ------------------------------------------------------- 4. suggested reply

def ai_draft_reply(ticket: dict, ticket_messages: list[dict]) -> dict:
    """4) Draft a reply for the administrator to review and approve; this service never sends it."""
    fallback = {
        "source": SOURCE_LOCAL,
        "model": None,
        "reply": services.smart_help(ticket)["reply"],
    }
    response = _chat_json(
        SYSTEM_RULES,
        json.dumps({
            "task": "Write a friendly support reply (3 sentences maximum)",
            "ticket": {
                "titre": ticket.get("title", ""),
                "description": ticket.get("description", ""),
            },
            "conversation": _transcript(ticket, ticket_messages, []),
            "instructions": ["greet the student with 'Hello,'", "say what will be checked",
                             "end with a follow-up promise or a specific question"],
            "response_format": {"reply": "the reply text in English"},
        }, ensure_ascii=False),
    )
    text = str((response or {}).get("reply") or "").strip()
    if not text:
        return fallback
    return {"source": SOURCE_AI, "model": settings.GROQ_MODEL, "reply": text}
