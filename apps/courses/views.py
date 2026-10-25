from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.users.decorators import roles_required
from apps.users.services import UserNotFoundError, UserStorageError, get_active_user

from .ai import AIRateLimitError, check_rate_limit
from .ai_eval.report import (
    AIReportInvalidError,
    AIReportNotFoundError,
    build_report_view,
    load_report,
)
from .forms import CATEGORY_LABELS, LEVEL_CHOICES, STATUS_CHOICES, CourseForm
from .images import (
    course_image_alt,
    course_image_url,
    delete_course_image,
    save_course_image,
)
from .services import (
    PUBLIC_SORT_CHOICES,
    CourseNotFoundError,
    CoursePermissionError,
    CourseStorageError,
    can_manage,
    create_course,
    delete_managed_course,
    get_course_detail,
    get_managed_course,
    list_public_courses,
    search_managed_courses,
    toggle_managed_course_status,
    update_managed_course,
)
from .tasks import (
    AIEvaluationAlreadyRunningError,
    AIEvaluationCooldownError,
    AIEvaluationError,
    AIEvaluationNotConfiguredError,
    dispatch_task,
    generate_course_content_task,
    generate_objectives_task,
    get_ai_evaluation_status,
    get_ai_execution_mode,
    get_task_result,
    improve_description_task,
    start_ai_evaluation,
    suggest_meta_task,
)
from .translation import (
    SUPPORTED_LANGUAGES,
    TranslationInputError,
    TranslationNotConfiguredError,
    TranslationRateLimitError,
    TranslationServiceError,
    TranslationTimeoutError,
    translate_course,
)

ROLE_LABELS = {"admin": "Administrateur", "instructor": "Instructeur", "student": "Étudiant"}


def _current_user(request) -> dict[str, Any] | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    try:
        return get_active_user(user_id)
    except (UserNotFoundError, UserStorageError):
        return None


def _role_label(user: dict[str, Any]) -> str:
    return ROLE_LABELS.get(user.get("role"), "Membre")


def _page_number(value: str | None) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


def _pagination_query(request) -> str:
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def _translation_rate_key(request, user: dict[str, Any] | None) -> str:
    if user:
        return f"translate:user:{user.get('_id')}"
    visitor = request.session.session_key or request.META.get("REMOTE_ADDR", "")
    return f"translate:visitor:{visitor}"


def _template_course(course: dict[str, Any]) -> dict[str, Any]:
    item = dict(course)
    item["id"] = str(course["_id"])
    stored_image = str(
        item.get("image") or item.get("thumbnail_url") or item.get("thumbnail") or ""
    ).strip()
    item["image"] = stored_image
    item["image_url"] = course_image_url(stored_image)
    item["image_alt"] = course_image_alt(item)
    # Legacy keys kept so existing templates and links keep working.
    item["thumbnail_url"] = item["image_url"]
    item["thumbnail"] = item["image_url"]
    item["duration_hours"] = item.get("duration_hours", 0)
    item["language"] = item.get("language", "fr")
    item["category_label"] = CATEGORY_LABELS.get(course.get("category"), course.get("category", ""))
    item["level_label"] = dict(LEVEL_CHOICES).get(course.get("level"), course.get("level", ""))
    return item


def _not_found(request) -> HttpResponse:
    return render(request, "404.html", status=404)


def _storage_error(request, message: str) -> HttpResponse:
    messages.error(request, message)
    return render(request, "backoffice/courses/error.html", {
        "user": request.mongo_user,
        "role_label": _role_label(request.mongo_user),
        "section": "courses",
    }, status=503)


def _store_uploaded_image(request, form: CourseForm) -> tuple[str, bool]:
    """Save the optional uploaded image; return (relative path, has_error)."""
    upload = form.cleaned_data.get("image")
    if not upload:
        return "", False
    try:
        return save_course_image(upload), False
    except ValidationError as error:
        form.add_error("image", error)
    except Exception:
        form.add_error("image", "Impossible d'enregistrer l'image. Réessayez.")
    form.fields["image"].widget.attrs["aria-invalid"] = "true"
    return "", True


def courses(request):
    search = (request.GET.get("q") or "").strip()[:100]
    category = request.GET.get("category", "")
    level = request.GET.get("level", "")
    sort_by = request.GET.get("sort", "recent")
    if category not in CATEGORY_LABELS:
        category = ""
    if level not in dict(LEVEL_CHOICES):
        level = ""
    if sort_by not in dict(PUBLIC_SORT_CHOICES):
        sort_by = "recent"
    page = _page_number(request.GET.get("page"))
    storage_error = False
    result = {
        "courses": [], "total": 0, "page": 1, "page_count": 1,
        "previous_page": None, "next_page": None,
    }
    try:
        result = list_public_courses(
            search=search, category=category, level=level, sort_by=sort_by, page=page
        )
    except CourseStorageError:
        storage_error = True
        messages.error(request, "Le catalogue des cours est temporairement indisponible.")

    context = {
        "page_title": "Cours",
        "courses": [_template_course(item) for item in result["courses"]],
        "total": result["total"],
        "page": result["page"],
        "page_count": result["page_count"],
        "previous_page": result["previous_page"],
        "next_page": result["next_page"],
        "search": search,
        "category": category,
        "level": level,
        "sort_by": sort_by,
        "category_choices": list(CATEGORY_LABELS.items()),
        "level_choices": list(LEVEL_CHOICES),
        "sort_choices": list(PUBLIC_SORT_CHOICES),
        "pagination_query": _pagination_query(request),
        "storage_error": storage_error,
    }
    return render(request, "frontoffice/courses/list.html", context, status=503 if storage_error else 200)


@require_GET
def course_detail(request, slug):
    user = _current_user(request)
    try:
        course = get_course_detail(slug, user)
    except CourseNotFoundError:
        return _not_found(request)
    except CourseStorageError:
        return render(request, "frontoffice/courses/detail.html", {
            "course": None,
            "storage_error": True,
        }, status=503)
    course_item = _template_course(course)
    return render(request, "frontoffice/courses/detail.html", {
        "course": course_item,
        "storage_error": False,
        "can_manage": can_manage(course, user),
        "translation_languages": [
            {"code": code, "label": label}
            for code, label in SUPPORTED_LANGUAGES.items()
            if code != course_item["language"]
        ],
    })


@require_POST
def course_translate(request, slug):
    user = _current_user(request)
    target = (request.POST.get("lang") or "").strip().lower()
    if target not in SUPPORTED_LANGUAGES:
        return JsonResponse(
            {"error": "Cette langue de traduction n'est pas prise en charge."}, status=400
        )

    try:
        course = get_course_detail(slug, user)
    except CourseNotFoundError:
        return JsonResponse({"error": "Cours introuvable."}, status=404)
    except CourseStorageError:
        return JsonResponse(
            {"error": "Le service des cours est temporairement indisponible."}, status=503
        )

    try:
        check_rate_limit(_translation_rate_key(request, user))
    except AIRateLimitError as error:
        return JsonResponse({"error": str(error)}, status=429)

    try:
        return JsonResponse(translate_course(course, target))
    except TranslationNotConfiguredError as error:
        return JsonResponse({"error": str(error)}, status=503)
    except TranslationRateLimitError as error:
        return JsonResponse({"error": str(error)}, status=429)
    except TranslationTimeoutError as error:
        return JsonResponse({"error": str(error)}, status=504)
    except TranslationInputError as error:
        return JsonResponse({"error": str(error)}, status=400)
    except TranslationServiceError as error:
        return JsonResponse({"error": str(error)}, status=502)


@roles_required("student")
@require_GET
def backoffice_courses(request):
    storage_error = False
    courses_list: list[dict[str, Any]] = []
    try:
        result = list_public_courses(page=1, page_size=50)
        courses_list = [_template_course(item) for item in result["courses"]]
    except CourseStorageError:
        storage_error = True
        messages.error(request, "Vos cours sont temporairement indisponibles.")
    return render(request, "backoffice/courses/student.html", {
        "user": request.mongo_user,
        "role_label": _role_label(request.mongo_user),
        "section": "courses",
        "title": "Mes cours",
        "courses": courses_list,
        "storage_error": storage_error,
    })


@roles_required("instructor", "admin")
@require_GET
def manage_course_list(request):
    search = (request.GET.get("q") or "").strip()[:100]
    status = request.GET.get("status", "")
    if status not in dict(STATUS_CHOICES):
        status = ""
    page = _page_number(request.GET.get("page"))
    storage_error = False
    result = {
        "courses": [], "total": 0, "page": 1, "page_count": 1,
        "previous_page": None, "next_page": None,
        "published_count": 0, "draft_count": 0,
    }
    try:
        result = search_managed_courses(
            request.mongo_user, search=search, status=status, page=page
        )
    except CourseStorageError:
        storage_error = True
        messages.error(request, "Vos cours sont temporairement indisponibles.")
    items = [_template_course(item) for item in result["courses"]]
    return render(request, "backoffice/courses/list.html", {
        "user": request.mongo_user,
        "role_label": _role_label(request.mongo_user),
        "section": "courses",
        "title": "Cours",
        "courses": items,
        "total": result["total"],
        "published_count": result["published_count"],
        "draft_count": result["draft_count"],
        "page": result["page"],
        "page_count": result["page_count"],
        "previous_page": result["previous_page"],
        "next_page": result["next_page"],
        "search": search,
        "status": status,
        "status_choices": list(STATUS_CHOICES),
        "pagination_query": _pagination_query(request),
        "storage_error": storage_error,
    })


def _managed_or_response(request, course_id):
    try:
        return get_managed_course(course_id, request.mongo_user), None
    except CourseNotFoundError:
        return None, _not_found(request)
    except CoursePermissionError:
        raise PermissionDenied("Accès refusé.")
    except CourseStorageError:
        return None, _storage_error(request, "Le service des cours est temporairement indisponible.")


@roles_required("instructor", "admin")
@require_GET
def manage_course_detail(request, course_id):
    course, response = _managed_or_response(request, course_id)
    if response:
        return response
    return render(request, "backoffice/courses/detail.html", {
        "user": request.mongo_user,
        "role_label": _role_label(request.mongo_user),
        "section": "courses",
        "title": "Détail du cours",
        "course": _template_course(course),
    })


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_course_create(request):
    form = CourseForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        data = form.to_document()
        image_path, image_error = _store_uploaded_image(request, form)
        if not image_error:
            data["image"] = image_path
            try:
                course = create_course(data=data, user=request.mongo_user)
            except CoursePermissionError:
                if image_path:
                    delete_course_image(image_path)
                raise PermissionDenied("Accès refusé.")
            except CourseStorageError as error:
                if image_path:
                    delete_course_image(image_path)
                messages.error(request, str(error))
                return render(request, "backoffice/courses/form.html", {
                    "user": request.mongo_user,
                    "role_label": _role_label(request.mongo_user),
                    "section": "courses",
                    "title": "Créer un cours",
                    "form": form,
                    "course": None,
                }, status=503)
            else:
                messages.success(request, "Le cours a été créé.")
                return redirect("users:course_manage_detail", course_id=str(course["_id"]))
    return render(request, "backoffice/courses/form.html", {
        "user": request.mongo_user,
        "role_label": _role_label(request.mongo_user),
        "section": "courses",
        "title": "Créer un cours",
        "form": form,
        "course": None,
    })


@roles_required("instructor", "admin")
@require_POST
def manage_course_ai_generate(request):
    user_id = str(request.mongo_user.get("_id", ""))
    try:
        check_rate_limit(user_id)
    except AIRateLimitError as err:
        return JsonResponse({"error": str(err)}, status=429)

    action = request.POST.get("action", "legacy")
    title = (request.POST.get("title") or "").strip()
    description = (request.POST.get("description") or "").strip()
    level = request.POST.get("level") or ""

    if not 3 <= len(title) <= 160:
        return JsonResponse({"error": "Le titre doit contenir entre 3 et 160 caractères."}, status=400)
    if len(description) > 5000:
        return JsonResponse({"error": "La description ne doit pas dépasser 5000 caractères."}, status=400)

    try:
        if action == "objectives":
            if level and level not in dict(LEVEL_CHOICES):
                return JsonResponse({"error": "Choisissez un niveau valide."}, status=400)
            task_id = dispatch_task(generate_objectives_task, title, description, level)
        elif action == "improve_description":
            if len(description) < 10:
                return JsonResponse(
                    {"error": "La description doit comporter au moins 10 caractères pour être améliorée."},
                    status=400,
                )
            task_id = dispatch_task(improve_description_task, title, description)
        elif action == "suggest_meta":
            task_id = dispatch_task(suggest_meta_task, title, description)
        else:
            if level not in dict(LEVEL_CHOICES):
                return JsonResponse({"error": "Choisissez un niveau valide."}, status=400)
            task_id = dispatch_task(generate_course_content_task, title, level)
    except Exception:
        return JsonResponse({"error": "Le service IA est indisponible."}, status=503)

    return JsonResponse({"task_id": task_id, "mode": get_ai_execution_mode()})


@roles_required("instructor", "admin")
@require_POST
def manage_course_ai_objectives(request):
    user_id = str(request.mongo_user.get("_id", ""))
    try:
        check_rate_limit(user_id)
    except AIRateLimitError as err:
        return JsonResponse({"error": str(err)}, status=429)

    title = (request.POST.get("title") or "").strip()
    description = (request.POST.get("description") or "").strip()
    level = request.POST.get("level") or ""

    if not 3 <= len(title) <= 160:
        return JsonResponse({"error": "Le titre doit contenir entre 3 et 160 caractères."}, status=400)
    if len(description) > 5000:
        return JsonResponse({"error": "La description ne doit pas dépasser 5000 caractères."}, status=400)
    if level and level not in dict(LEVEL_CHOICES):
        return JsonResponse({"error": "Choisissez un niveau valide."}, status=400)

    try:
        task_id = dispatch_task(generate_objectives_task, title, description, level)
    except Exception:
        return JsonResponse({"error": "Le service IA est indisponible."}, status=503)
    return JsonResponse({"task_id": task_id, "mode": get_ai_execution_mode()})


@roles_required("instructor", "admin")
@require_POST
def manage_course_ai_improve_description(request):
    user_id = str(request.mongo_user.get("_id", ""))
    try:
        check_rate_limit(user_id)
    except AIRateLimitError as err:
        return JsonResponse({"error": str(err)}, status=429)

    title = (request.POST.get("title") or "").strip()
    description = (request.POST.get("description") or "").strip()

    if not 3 <= len(title) <= 160:
        return JsonResponse({"error": "Le titre doit contenir entre 3 et 160 caractères."}, status=400)
    if not 10 <= len(description) <= 5000:
        return JsonResponse(
            {"error": "La description doit contenir entre 10 et 5000 caractères pour être améliorée."},
            status=400,
        )

    try:
        task_id = dispatch_task(improve_description_task, title, description)
    except Exception:
        return JsonResponse({"error": "Le service IA est indisponible."}, status=503)
    return JsonResponse({"task_id": task_id, "mode": get_ai_execution_mode()})


@roles_required("instructor", "admin")
@require_POST
def manage_course_ai_suggest_meta(request):
    user_id = str(request.mongo_user.get("_id", ""))
    try:
        check_rate_limit(user_id)
    except AIRateLimitError as err:
        return JsonResponse({"error": str(err)}, status=429)

    title = (request.POST.get("title") or "").strip()
    description = (request.POST.get("description") or "").strip()

    if not 3 <= len(title) <= 160:
        return JsonResponse({"error": "Le titre doit contenir entre 3 et 160 caractères."}, status=400)
    if len(description) > 5000:
        return JsonResponse({"error": "La description ne doit pas dépasser 5000 caractères."}, status=400)

    try:
        task_id = dispatch_task(suggest_meta_task, title, description)
    except Exception:
        return JsonResponse({"error": "Le service IA est indisponible."}, status=503)
    return JsonResponse({"task_id": task_id, "mode": get_ai_execution_mode()})


@roles_required("instructor", "admin")
@require_POST
def manage_course_ai_status(request, task_id):
    if not task_id or len(task_id) > 100:
        return JsonResponse({"status": "error", "error": "Identifiant de tâche invalide."}, status=400)
    try:
        data = get_task_result(task_id)
    except Exception:
        return JsonResponse(
            {"status": "error", "error": "Impossible de récupérer le statut de la génération."},
            status=503,
        )
    return JsonResponse(data)


@roles_required("instructor", "admin")
@require_GET
def manage_course_ai_report(request):
    report = None
    report_missing = False
    report_invalid = False
    try:
        report = build_report_view(load_report())
    except AIReportNotFoundError:
        report_missing = True
    except AIReportInvalidError:
        report_invalid = True
    return render(request, "backoffice/courses/ai_report.html", {
        "user": request.mongo_user,
        "role_label": _role_label(request.mongo_user),
        "section": "courses",
        "title": "Rapport IA",
        "report": report,
        "report_missing": report_missing,
        "report_invalid": report_invalid,
        "can_run_evaluation": request.mongo_user.get("role") == "admin",
    })


@roles_required("admin")
@require_POST
def manage_course_ai_report_run(request):
    try:
        task_id = start_ai_evaluation()
    except AIEvaluationAlreadyRunningError as error:
        return JsonResponse({"error": str(error)}, status=409)
    except AIEvaluationCooldownError as error:
        return JsonResponse(
            {
                "error": str(error),
                "remaining_seconds": error.remaining_seconds,
            },
            status=429,
        )
    except AIEvaluationNotConfiguredError as error:
        return JsonResponse({"error": str(error)}, status=503)
    except AIEvaluationError as error:
        return JsonResponse({"error": str(error)}, status=503)
    except Exception:
        return JsonResponse(
            {"error": "Le service d'évaluation est indisponible. Réessayez plus tard."},
            status=503,
        )
    return JsonResponse({"task_id": task_id})


@roles_required("admin")
@require_GET
def manage_course_ai_report_status(request, task_id):
    if not task_id or len(task_id) > 100:
        return JsonResponse(
            {
                "state": "error",
                "done": 0,
                "total": 0,
                "error_code": "invalid_task_id",
            },
            status=400,
        )
    return JsonResponse(get_ai_evaluation_status(task_id))


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_course_edit(request, course_id):
    course, response = _managed_or_response(request, course_id)
    if response:
        return response
    form = CourseForm(request.POST or None, request.FILES or None, course=course)
    if request.method == "POST" and form.is_valid():
        data = form.to_document()
        image_path, image_error = _store_uploaded_image(request, form)
        if not image_error:
            if image_path:
                data["image"] = image_path
            try:
                updated = update_managed_course(
                    course_id, data=data, user=request.mongo_user
                )
            except CourseNotFoundError:
                if image_path:
                    delete_course_image(image_path)
                return _not_found(request)
            except CoursePermissionError:
                if image_path:
                    delete_course_image(image_path)
                raise PermissionDenied("Accès refusé.")
            except CourseStorageError as error:
                if image_path:
                    delete_course_image(image_path)
                messages.error(request, str(error))
                return render(request, "backoffice/courses/form.html", {
                    "user": request.mongo_user,
                    "role_label": _role_label(request.mongo_user),
                    "section": "courses",
                    "title": "Modifier le cours",
                    "form": form,
                    "course": _template_course(course),
                }, status=503)
            else:
                messages.success(request, "Le cours a été modifié.")
                return redirect("users:course_manage_detail", course_id=str(updated["_id"]))
    return render(request, "backoffice/courses/form.html", {
        "user": request.mongo_user,
        "role_label": _role_label(request.mongo_user),
        "section": "courses",
        "title": "Modifier le cours",
        "form": form,
        "course": _template_course(course),
    })


@roles_required("instructor", "admin")
@require_POST
def manage_course_publish(request, course_id):
    try:
        course = toggle_managed_course_status(course_id, user=request.mongo_user)
    except CourseNotFoundError:
        return _not_found(request)
    except CoursePermissionError:
        raise PermissionDenied("Accès refusé.")
    except CourseStorageError as error:
        messages.error(request, str(error))
        return redirect("users:course_manage_list")
    if course["status"] == "published":
        messages.success(request, "Le cours est maintenant publié.")
    else:
        messages.success(request, "Le cours est repassé en brouillon.")
    return redirect("users:course_manage_detail", course_id=str(course["_id"]))


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_course_delete(request, course_id):
    course, response = _managed_or_response(request, course_id)
    if response:
        return response
    if request.method == "POST":
        try:
            delete_managed_course(course_id, user=request.mongo_user)
        except CourseNotFoundError:
            return _not_found(request)
        except CoursePermissionError:
            raise PermissionDenied("Accès refusé.")
        except CourseStorageError as error:
            messages.error(request, str(error))
            return render(request, "backoffice/courses/delete.html", {
                "user": request.mongo_user,
                "role_label": _role_label(request.mongo_user),
                "section": "courses",
                "title": "Supprimer le cours",
                "course": _template_course(course),
            }, status=503)
        else:
            messages.success(request, "Le cours a été supprimé.")
            return redirect("users:course_manage_list")
    return render(request, "backoffice/courses/delete.html", {
        "user": request.mongo_user,
        "role_label": _role_label(request.mongo_user),
        "section": "courses",
        "title": "Supprimer le cours",
        "course": _template_course(course),
    })
