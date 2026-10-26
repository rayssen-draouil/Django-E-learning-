import json
import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.contrib import messages
from django.http import Http404, HttpResponseForbidden, HttpResponseServerError, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.users.decorators import roles_required, user_required
from .ai.gemini_client import GeminiConfigurationError, GeminiGenerationError
from .ai.quiz_generator import InvalidGeneratedQuiz, generate_quiz
from .forms import QuizAIGenerationForm, QuizForm, QuizImportForm, QuizSubmissionForm
from .services import (
    QuizNotFoundError,
    QuizPermissionError,
    QuizStorageError,
    QuizSubmissionError,
    create_quiz,
    delete_managed_quiz,
    get_managed_quiz,
    get_published_quiz,
    get_student_attempt,
    list_author_quizzes,
    list_published_quizzes,
    publish_managed_quiz,
    submit_quiz,
    update_managed_quiz,
)


logger = logging.getLogger(__name__)


class QuizAIRateLimitError(Exception):
    pass


def _consume_ai_generation_slot(user):
    user_digest = hashlib.sha256(str(user["_id"]).encode("utf-8")).hexdigest()
    key = f"quiz-ai-generation:{user_digest}"
    window = settings.QUIZ_AI_GENERATION_WINDOW_SECONDS
    def increment_or_recover():
        try:
            count = cache.incr(key)
        except ValueError:
            if cache.add(key, 1, timeout=window):
                return 1 <= settings.QUIZ_AI_GENERATION_LIMIT
            count = cache.incr(key)
        if count > settings.QUIZ_AI_GENERATION_LIMIT:
            cache.decr(key)
            return False
        return True

    try:
        if cache.add(key, 1, timeout=window):
            return settings.QUIZ_AI_GENERATION_LIMIT >= 1
        return increment_or_recover()
    except Exception as error:
        raise QuizAIRateLimitError from error


def _template_quiz(document):
    quiz = dict(document)
    quiz["id"] = str(quiz["_id"])
    quiz["questions"] = []
    for source in document.get("questions", []):
        question = dict(source)
        question["options"] = [{**option, "id": str(option["_id"])} for option in source.get("options", [])]
        question["correct_option_ids"] = [str(value) for value in source.get("correct_option_ids", [])]
        quiz["questions"].append(question)
    return quiz


def quiz(request):
    try:
        documents = list_published_quizzes()
        quizzes = [{
            "id": str(item["_id"]),
            "title": item["title"],
            "description": item.get("description", ""),
            "question_count": len(item.get("questions", [])),
        } for item in documents]
        items = [(item["title"], item["description"], f'{item["question_count"]} questions') for item in quizzes]
    except QuizStorageError:
        quizzes, items = [], []
        messages.error(request, "Quiz management is temporarily unavailable.")
    return render(request, "frontoffice/learning_page.html", {
        "page_title": "Quiz management",
        "page_intro": "Test your knowledge and find skills to improve.",
        "items": items,
        "quizzes": quizzes,
        "item_label": "quiz",
    })


@user_required
def backoffice_quizzes(request):
    """Keep the legacy route while using the unified role-aware dashboard."""
    return redirect("users:backoffice")

@roles_required("student", "instructor", "admin")
@require_http_methods(["GET", "POST"])
def take_quiz(request, quiz_id):
    try:
        document = get_published_quiz(quiz_id)
    except QuizNotFoundError:
        raise Http404
    except QuizStorageError:
        return HttpResponseServerError("Quiz management is temporarily unavailable.")

    form = QuizSubmissionForm(request.POST or None, quiz=document)
    duration_seconds = document.get("duration_seconds")
    deadline = None
    time_expired = False
    if duration_seconds:
        deadline_key = f"quiz_deadline_{document['_id']}"
        if request.method == "GET" and request.GET.get("restart") == "1":
            request.session.pop(deadline_key, None)
        deadline = request.session.get(deadline_key)
        if deadline is None:
            deadline = int(time.time()) + int(duration_seconds)
            request.session[deadline_key] = deadline
        time_expired = request.method == "POST" and time.time() > deadline + 30

    if time_expired:
        form.add_error(None, "Time is up. Reload the page to start a new attempt.")
    elif request.method == "POST" and form.is_valid():
        try:
            attempt_id = submit_quiz(
                quiz_id,
                user=request.mongo_user,
                answers=form.selected_answers(),
            )
        except QuizNotFoundError:
            raise Http404
        except QuizSubmissionError as error:
            form.add_error(None, str(error))
        except QuizStorageError:
            return HttpResponseServerError("Your result could not be saved right now.")
        else:
            if duration_seconds:
                request.session.pop(f"quiz_deadline_{document['_id']}", None)
            return redirect("users:quiz_result", attempt_id=str(attempt_id))

    quiz_questions = [{
        "id": str(question["_id"]),
        "position": question["position"],
        "prompt": question["prompt"],
        "points": question["points"],
        "type": question["type"],
        "field_name": f"question_{question['_id']}",
    } for question in document.get("questions", [])]
    for question in quiz_questions:
        question["form_field"] = form[question["field_name"]]
    remaining_seconds = max(0, int(deadline - time.time() + 0.999)) if deadline else None

    public_quiz = {
        "id": str(document["_id"]),
        "title": document["title"],
        "description": document.get("description", ""),
        "duration_seconds": document.get("duration_seconds"),
        "questions": quiz_questions,
        "remaining_seconds": remaining_seconds,
        "timer_elapsed": remaining_seconds == 0 if remaining_seconds is not None else False,
        "time_expired": time_expired,
    }
    return render(request, "frontoffice/quizzes/take.html", {
        "user": request.mongo_user,
        "quiz": public_quiz,
        "form": form,
    })


@roles_required("student", "instructor", "admin")
@require_GET
def quiz_result(request, attempt_id):
    try:
        attempt = get_student_attempt(attempt_id, request.mongo_user)
    except QuizNotFoundError:
        raise Http404
    except QuizStorageError:
        return HttpResponseServerError("Your results are temporarily unavailable.")

    for answer in attempt["answers"]:
        answer["selected_option_ids"] = [str(value) for value in answer["selected_option_ids"]]
        answer["correct_option_ids"] = [str(value) for value in answer["correct_option_ids"]]
        answer["options"] = [{**option, "id": str(option["_id"])} for option in answer["options"]]
    return render(request, "frontoffice/quizzes/result.html", {
        "user": request.mongo_user,
        "attempt": attempt,
    })


@roles_required("instructor", "admin")
@require_GET
def manage_quiz_list(request):
    try:
        quizzes = [_template_quiz(item) for item in list_author_quizzes(request.mongo_user)]
    except QuizStorageError:
        return render(request, "backoffice/quizzes/list.html", {
            "user": request.mongo_user, "quizzes": [], "title": "Quiz management",
            "section": "quizzes", "storage_error": True,
            "quiz_summary": {"total": 0, "drafts": 0, "published": 0, "questions": 0},
        }, status=503)
    quiz_summary = {
        "total": len(quizzes),
        "drafts": sum(quiz.get("status") == "draft" for quiz in quizzes),
        "published": sum(quiz.get("status") == "published" for quiz in quizzes),
        "questions": sum(len(quiz.get("questions", [])) for quiz in quizzes),
    }
    return render(request, "backoffice/quizzes/list.html", {
        "user": request.mongo_user,
        "quizzes": quizzes,
        "title": "Quiz management",
        "section": "quizzes",
        "quiz_summary": quiz_summary,
    })


def _managed_or_response(request, quiz_id):
    try:
        return get_managed_quiz(quiz_id, request.mongo_user), None
    except QuizNotFoundError:
        raise Http404
    except QuizPermissionError:
        return None, HttpResponseForbidden("Access denied.")
    except QuizStorageError:
        return None, HttpResponseServerError("Quiz storage is temporarily unavailable.")


@roles_required("instructor", "admin")
@require_GET
def manage_quiz_detail(request, quiz_id):
    document, response = _managed_or_response(request, quiz_id)
    if response:
        return response
    document = _template_quiz(document)
    return render(request, "backoffice/quizzes/detail.html", {
        "user": request.mongo_user, "quiz": document,
        "title": document["title"], "section": "quizzes",
        "total_points": sum(question.get("points", 0) for question in document.get("questions", [])),
    })


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_quiz_create(request):
    import_form = QuizImportForm()
    form = QuizForm(request.POST or None)
    if request.method == "POST" and "import_json" in request.POST:
        form = QuizForm()
        import_form = QuizImportForm(request.POST, request.FILES)
        if import_form.is_valid():
            source = import_form.cleaned_data["quiz_file"]
            candidate_data = {
                "title": source.get("title", ""),
                "description": source.get("description", ""),
                "status": "draft",
                "duration_seconds": source.get("duration_seconds") if source.get("duration_seconds") is not None else "",
                "passing_score": source.get("passing_score") if source.get("passing_score") is not None else "",
                "questions_json": json.dumps(source["questions"], ensure_ascii=False),
            }
            candidate = QuizForm(candidate_data)
            if candidate.is_valid():
                try:
                    quiz_id = create_quiz(data=candidate.to_document(), user=request.mongo_user)
                except QuizStorageError:
                    form = candidate
                    messages.error(request, "The file is valid, but the draft could not be saved.")
                else:
                    messages.success(request, "The quiz was imported as a draft. Review it before publishing.")
                    return redirect("users:quiz_manage_detail", quiz_id=str(quiz_id))
            else:
                form = candidate
    elif request.method == "POST" and form.is_valid():
        try:
            quiz_id = create_quiz(data=form.to_document(), user=request.mongo_user)
        except QuizStorageError:
            messages.error(request, "The quiz could not be saved right now.")
        else:
            messages.success(request, "The quiz was created.")
            return redirect("users:quiz_manage_detail", quiz_id=str(quiz_id))
    return render(request, "backoffice/quizzes/create.html", {
        "user": request.mongo_user, "form": form,
        "import_form": import_form,
        "title": "Create a quiz", "section": "quizzes",
    })


def _json_request_data(request):
    if len(request.body) > 500_000:
        return None
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@roles_required("instructor", "admin")
@require_POST
def generate_quiz_with_ai(request):
    payload = _json_request_data(request)
    if payload is None:
        return JsonResponse({"error": "The request is invalid or too large."}, status=400)
    form = QuizAIGenerationForm(payload)
    if not form.is_valid():
        return JsonResponse({
            "error": "Check the generation settings.",
            "field_errors": form.errors.get_json_data(),
        }, status=400)

    try:
        if not _consume_ai_generation_slot(request.mongo_user):
            response = JsonResponse({
                "error": "The limit has been reached: five generations are allowed per hour. Please try again later."
            }, status=429)
            response["Retry-After"] = str(settings.QUIZ_AI_GENERATION_WINDOW_SECONDS)
            return response
    except QuizAIRateLimitError:
        return JsonResponse({"error": "Rate limiting is temporarily unavailable."}, status=503)

    try:
        generated = generate_quiz(form.cleaned_data)
    except GeminiConfigurationError:
        return JsonResponse({"error": "The AI generator is not configured on this server."}, status=503)
    except GeminiGenerationError:
        return JsonResponse({
            "error": "Google Gemini did not respond. Check the API key, configured model, API access, and quota. See the Django terminal for server diagnostics."
        }, status=502)
    except InvalidGeneratedQuiz as error:
        reason = error.args[0] if error.args else "invalid_generated_quiz"
        logger.warning("Rejected Gemini quiz response (reason=%s)", reason)
        return JsonResponse({
            "error": "Gemini responded, but the generated quiz did not pass validation. Try again or reduce the number or type of questions."
        }, status=502)

    validation_form = QuizForm({
        "title": generated["title"],
        "description": generated["description"],
        "status": "draft",
        "duration_seconds": "",
        "passing_score": "",
        "questions_json": json.dumps(generated["questions"], ensure_ascii=False),
    })
    if not validation_form.is_valid():
        return JsonResponse({"error": "The generated response does not match the quiz format. Generate it again."}, status=502)
    duration_min = form.cleaned_data.get("duration_minutes")
    if duration_min is not None and duration_min != "":
        try:
            d_min = int(duration_min)
            generated["duration_minutes"] = d_min
            generated["duration_seconds"] = d_min * 60 if d_min > 0 else None
        except (ValueError, TypeError):
            generated["duration_minutes"] = 15
            generated["duration_seconds"] = 900
    else:
        generated["duration_minutes"] = 0
        generated["duration_seconds"] = None
    return JsonResponse({"quiz": generated}, status=200)


@roles_required("instructor", "admin")
@require_POST
def create_ai_quiz_draft(request):
    payload = _json_request_data(request)
    if payload is None:
        return JsonResponse({"error": "The quiz data is invalid or too large."}, status=400)
    try:
        questions_json = json.dumps(payload.get("questions"), ensure_ascii=False)
    except (TypeError, ValueError):
        return JsonResponse({"error": "The question list is invalid."}, status=400)
    duration_val = ""
    raw_duration = payload.get("duration_seconds")
    if raw_duration in (None, "", "null") and "duration_minutes" in payload:
        try:
            mins = int(payload.get("duration_minutes"))
            raw_duration = mins * 60 if mins > 0 else ""
        except (ValueError, TypeError):
            raw_duration = ""

    if raw_duration not in (None, "", "null", 0, "0"):
        try:
            parsed = int(raw_duration)
            if parsed > 0:
                duration_val = parsed
        except (ValueError, TypeError):
            duration_val = ""

    form = QuizForm({
        "title": payload.get("title", ""),
        "description": payload.get("description", ""),
        "status": "draft",
        "duration_seconds": duration_val,
        "passing_score": "",
        "questions_json": questions_json,
    })
    if not form.is_valid():
        return JsonResponse({
            "error": "The quiz contains invalid data. Fix it and try again.",
            "field_errors": form.errors.get_json_data(),
        }, status=400)
    try:
        quiz_id = create_quiz(data=form.to_document(), user=request.mongo_user)
    except QuizStorageError:
        return JsonResponse({"error": "The draft could not be saved right now."}, status=503)

    messages.success(request, "The AI-generated quiz was saved as a draft.")
    return JsonResponse({
        "redirect_url": reverse("users:quiz_manage_detail", kwargs={"quiz_id": str(quiz_id)})
    }, status=201)


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_quiz_edit(request, quiz_id):
    document, response = _managed_or_response(request, quiz_id)
    if response:
        return response
    form = QuizForm(request.POST or None, quiz=document)
    if request.method == "POST" and form.is_valid():
        try:
            update_managed_quiz(quiz_id, data=form.to_document(), user=request.mongo_user)
        except QuizNotFoundError:
            raise Http404
        except QuizPermissionError:
            return HttpResponseForbidden("Access denied.")
        except QuizStorageError:
            messages.error(request, "Changes could not be saved.")
        else:
            messages.success(request, "The quiz was updated.")
            return redirect("users:quiz_manage_detail", quiz_id=str(document["_id"]))
    return render(request, "backoffice/quizzes/edit.html", {
        "user": request.mongo_user, "quiz": _template_quiz(document), "form": form,
        "title": "Edit quiz", "section": "quizzes",
    })


@roles_required("instructor", "admin")
@require_http_methods(["POST"])
def manage_quiz_publish(request, quiz_id):
    try:
        publish_managed_quiz(quiz_id, user=request.mongo_user)
    except QuizNotFoundError:
        raise Http404
    except QuizPermissionError:
        return HttpResponseForbidden("Access denied.")
    except QuizSubmissionError as error:
        messages.error(request, str(error))
    except QuizStorageError:
        messages.error(request, "The quiz could not be published right now.")
    else:
        messages.success(request, "The quiz was submitted and published.")
    return redirect("users:quiz_manage_detail", quiz_id=quiz_id)


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_quiz_delete(request, quiz_id):
    document, response = _managed_or_response(request, quiz_id)
    if response:
        return response
    if request.method == "POST":
        try:
            delete_managed_quiz(quiz_id, user=request.mongo_user)
        except QuizNotFoundError:
            raise Http404
        except QuizPermissionError:
            return HttpResponseForbidden("Access denied.")
        except QuizStorageError:
            messages.error(request, "The quiz could not be deleted right now.")
        else:
            messages.success(request, "The quiz was deleted.")
            return redirect("users:quiz_manage_list")
    return render(request, "backoffice/quizzes/delete.html", {
        "user": request.mongo_user, "quiz": _template_quiz(document),
        "title": "Delete quiz", "section": "quizzes",
    })
