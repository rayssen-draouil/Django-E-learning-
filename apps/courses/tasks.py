import time

from celery import shared_task

from .ai import AIError, generate_course_content, record_ai_log


@shared_task
def generate_course_content_task(title: str, level: str) -> dict:
    started = time.monotonic()
    try:
        content = generate_course_content(title, level)
    except AIError as error:
        record_ai_log(
            duration_ms=int((time.monotonic() - started) * 1000),
            success=False,
            error=str(error),
        )
        return {"ok": False, "error": str(error)}
    except Exception:
        record_ai_log(
            duration_ms=int((time.monotonic() - started) * 1000),
            success=False,
            error="Erreur inattendue de l'assistant IA.",
        )
        return {"ok": False, "error": "Erreur inattendue de l'assistant IA."}
    record_ai_log(
        duration_ms=int((time.monotonic() - started) * 1000),
        success=True,
        output_length=len(content["description"]),
    )
    return {"ok": True, **content}
