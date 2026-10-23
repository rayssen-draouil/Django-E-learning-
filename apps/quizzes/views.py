import json
import time

from django.contrib import messages
from django.http import Http404, HttpResponseForbidden, HttpResponseServerError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from apps.users.decorators import roles_required, user_required
from .forms import QuizForm, QuizImportForm, QuizSubmissionForm
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
        messages.error(request, "Les quiz sont temporairement indisponibles.")
    return render(request, "frontoffice/learning_page.html", {
        "page_title": "Quiz",
        "page_intro": "Testez vos connaissances et identifiez les compétences à renforcer.",
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
        return HttpResponseServerError("Les quiz sont temporairement indisponibles.")

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
        form.add_error(None, "Le délai est dépassé. Rechargez la page pour démarrer une nouvelle tentative.")
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
            return HttpResponseServerError("Impossible d'enregistrer votre résultat pour le moment.")
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
        return HttpResponseServerError("Vos résultats sont temporairement indisponibles.")

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
        quizzes = list_author_quizzes(request.mongo_user)
    except QuizStorageError:
        return render(request, "backoffice/quizzes/list.html", {
            "user": request.mongo_user, "quizzes": [], "title": "Mes quiz",
            "section": "quizzes", "storage_error": True,
        }, status=503)
    return render(request, "backoffice/quizzes/list.html", {
        "user": request.mongo_user,
        "quizzes": [_template_quiz(item) for item in quizzes],
        "title": "Mes quiz",
        "section": "quizzes",
    })


def _managed_or_response(request, quiz_id):
    try:
        return get_managed_quiz(quiz_id, request.mongo_user), None
    except QuizNotFoundError:
        raise Http404
    except QuizPermissionError:
        return None, HttpResponseForbidden("Accès refusé.")
    except QuizStorageError:
        return None, HttpResponseServerError("Le stockage des quiz est temporairement indisponible.")


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
                    messages.error(request, "Le fichier est valide, mais le brouillon n'a pas pu être enregistré.")
                else:
                    messages.success(request, "Le quiz a été importé en brouillon. Vérifiez son contenu avant publication.")
                    return redirect("users:quiz_manage_detail", quiz_id=str(quiz_id))
            else:
                form = candidate
    elif request.method == "POST" and form.is_valid():
        try:
            quiz_id = create_quiz(data=form.to_document(), user=request.mongo_user)
        except QuizStorageError:
            messages.error(request, "Impossible d'enregistrer le quiz pour le moment.")
        else:
            messages.success(request, "Le quiz a été créé.")
            return redirect("users:quiz_manage_detail", quiz_id=str(quiz_id))
    return render(request, "backoffice/quizzes/create.html", {
        "user": request.mongo_user, "form": form,
        "import_form": import_form,
        "title": "Créer un quiz", "section": "quizzes",
    })


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
            return HttpResponseForbidden("Accès refusé.")
        except QuizStorageError:
            messages.error(request, "Impossible d'enregistrer les modifications.")
        else:
            messages.success(request, "Le quiz a été modifié.")
            return redirect("users:quiz_manage_detail", quiz_id=str(document["_id"]))
    return render(request, "backoffice/quizzes/edit.html", {
        "user": request.mongo_user, "quiz": _template_quiz(document), "form": form,
        "title": "Modifier le quiz", "section": "quizzes",
    })


@roles_required("instructor", "admin")
@require_http_methods(["POST"])
def manage_quiz_publish(request, quiz_id):
    try:
        publish_managed_quiz(quiz_id, user=request.mongo_user)
    except QuizNotFoundError:
        raise Http404
    except QuizPermissionError:
        return HttpResponseForbidden("Accès refusé.")
    except QuizSubmissionError as error:
        messages.error(request, str(error))
    except QuizStorageError:
        messages.error(request, "Impossible de soumettre le quiz pour le moment.")
    else:
        messages.success(request, "Le quiz a été soumis et publié.")
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
            return HttpResponseForbidden("Accès refusé.")
        except QuizStorageError:
            messages.error(request, "Impossible de supprimer le quiz pour le moment.")
        else:
            messages.success(request, "Le quiz a été supprimé.")
            return redirect("users:quiz_manage_list")
    return render(request, "backoffice/quizzes/delete.html", {
        "user": request.mongo_user, "quiz": _template_quiz(document),
        "title": "Supprimer le quiz", "section": "quizzes",
    })
