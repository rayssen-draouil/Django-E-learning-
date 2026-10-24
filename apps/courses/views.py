from __future__ import annotations

from typing import Any

from celery.result import AsyncResult
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.users.decorators import roles_required
from apps.users.services import UserNotFoundError, UserStorageError, get_active_user

from .forms import CATEGORY_LABELS, LEVEL_CHOICES, CourseForm
from .services import (
    CourseNotFoundError,
    CoursePermissionError,
    CourseStorageError,
    can_manage,
    create_course,
    delete_managed_course,
    get_course_detail,
    get_managed_course,
    list_managed_courses,
    list_public_courses,
    toggle_managed_course_status,
    update_managed_course,
)
from .tasks import generate_course_content_task

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


def _template_course(course: dict[str, Any]) -> dict[str, Any]:
    item = dict(course)
    item["id"] = str(course["_id"])
    item["thumbnail_url"] = item.get("thumbnail_url") or item.get("thumbnail", "")
    item["thumbnail"] = item["thumbnail_url"]
    item["duration_hours"] = item.get("duration_hours", 0)
    item["language"] = item.get("language", "fr")
    item["category_label"] = CATEGORY_LABELS.get(course.get("category"), course.get("category", ""))
    item["level_label"] = dict(LEVEL_CHOICES).get(course.get("level"), course.get("level", ""))
    return item


def _storage_error(request, message: str) -> HttpResponse:
    messages.error(request, message)
    return render(request, "backoffice/courses/error.html", {
        "user": request.mongo_user,
        "role_label": _role_label(request.mongo_user),
        "section": "courses",
    }, status=503)


def courses(request):
    search = (request.GET.get("q") or "").strip()[:100]
    category = request.GET.get("category", "")
    level = request.GET.get("level", "")
    if category not in CATEGORY_LABELS:
        category = ""
    if level not in dict(LEVEL_CHOICES):
        level = ""
    page = _page_number(request.GET.get("page"))
    storage_error = False
    result = {
        "courses": [], "total": 0, "page": 1, "page_count": 1,
        "previous_page": None, "next_page": None,
    }
    try:
        result = list_public_courses(
            search=search, category=category, level=level, page=page
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
        "category_choices": list(CATEGORY_LABELS.items()),
        "level_choices": list(LEVEL_CHOICES),
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
        raise Http404("Cours introuvable.")
    except CourseStorageError:
        return render(request, "frontoffice/courses/detail.html", {
            "course": None,
            "storage_error": True,
        }, status=503)
    return render(request, "frontoffice/courses/detail.html", {
        "course": _template_course(course),
        "storage_error": False,
        "can_manage": can_manage(course, user),
    })


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
    storage_error = False
    managed_courses: list[dict[str, Any]] = []
    try:
        managed_courses = list_managed_courses(request.mongo_user)
    except CourseStorageError:
        storage_error = True
        messages.error(request, "Vos cours sont temporairement indisponibles.")
    items = [_template_course(item) for item in managed_courses]
    return render(request, "backoffice/courses/list.html", {
        "user": request.mongo_user,
        "role_label": _role_label(request.mongo_user),
        "section": "courses",
        "title": "Mes cours",
        "courses": items,
        "total": len(items),
        "published_count": sum(item.get("status") == "published" for item in items),
        "draft_count": sum(item.get("status") == "draft" for item in items),
        "storage_error": storage_error,
    })


def _managed_or_response(request, course_id):
    try:
        return get_managed_course(course_id, request.mongo_user), None
    except CourseNotFoundError:
        raise Http404("Cours introuvable.")
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
    form = CourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            course = create_course(data=form.to_document(), user=request.mongo_user)
        except CoursePermissionError:
            raise PermissionDenied("Accès refusé.")
        except CourseStorageError as error:
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
    title = (request.POST.get("title") or "").strip()
    level = request.POST.get("level") or ""
    if not 3 <= len(title) <= 160:
        return JsonResponse({"error": "Le titre doit contenir entre 3 et 160 caractères."}, status=400)
    if level not in dict(LEVEL_CHOICES):
        return JsonResponse({"error": "Choisissez un niveau valide."}, status=400)
    try:
        task = generate_course_content_task.delay(title, level)
    except Exception:
        return JsonResponse({"error": "Le service IA est indisponible."}, status=503)
    return JsonResponse({"task_id": task.id})


@roles_required("instructor", "admin")
@require_GET
def manage_course_ai_status(request, task_id):
    result = AsyncResult(task_id)
    if result.failed():
        return JsonResponse({"status": "error", "error": "La génération IA a échoué."})
    if result.successful():
        data = result.result or {}
        if data.get("ok"):
            return JsonResponse({
                "status": "done",
                "description": data.get("description", ""),
                "objectives": data.get("objectives", []),
            })
        return JsonResponse({
            "status": "error",
            "error": data.get("error", "La génération IA a échoué."),
        })
    return JsonResponse({"status": "pending"})


@roles_required("instructor", "admin")
@require_http_methods(["GET", "POST"])
def manage_course_edit(request, course_id):
    course, response = _managed_or_response(request, course_id)
    if response:
        return response
    form = CourseForm(request.POST or None, course=course)
    if request.method == "POST" and form.is_valid():
        try:
            updated = update_managed_course(
                course_id, data=form.to_document(), user=request.mongo_user
            )
        except CourseNotFoundError:
            raise Http404("Cours introuvable.")
        except CoursePermissionError:
            raise PermissionDenied("Accès refusé.")
        except CourseStorageError as error:
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
        raise Http404("Cours introuvable.")
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
            raise Http404("Cours introuvable.")
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
