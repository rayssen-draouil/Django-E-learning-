from django.contrib import messages
from django.shortcuts import redirect, render

from apps.quizzes.services import QuizStorageError, get_student_quiz_stats, list_author_quizzes, list_published_quizzes
from .decorators import user_required
from .forms import LoginForm, ProfileForm, RegistrationForm
from .services import (
    DuplicateEmailError,
    UserStorageError,
    authenticate_user,
    create_user,
    update_user_profile,
)


@user_required
def backoffice(request):
    role = request.mongo_user.get("role", "student")
    quiz_stats = {"attempts": [], "attempt_count": 0, "quiz_count": 0,
                  "average_score": 0, "best_score": 0, "pass_rate": 0, "question_stats": []}
    available_quizzes, managed_quizzes, storage_error = [], [], False
    try:
        quiz_stats = get_student_quiz_stats(request.mongo_user)
        available_quizzes = list_published_quizzes()
        if role in {"instructor", "admin"}:
            managed_quizzes = list_author_quizzes(request.mongo_user)
    except QuizStorageError:
        storage_error = True
        messages.error(request, "Les donn\u00e9es Quiz sont temporairement indisponibles.")

    if role in {"instructor", "admin"}:
        dashboard_stats = [
            ("Quiz g\u00e9r\u00e9s", str(len(managed_quizzes)), "light-blue"),
            ("Brouillons", str(sum(item.get("status") == "draft" for item in managed_quizzes)), "light-orange"),
            ("Quiz publi\u00e9s", str(sum(item.get("status") == "published" for item in managed_quizzes)), "light-purple"),
            ("Tentatives personnelles", str(quiz_stats["attempt_count"]), "light-green"),
        ]
    else:
        dashboard_stats = [
            ("Quiz disponibles", str(len(available_quizzes)), "light-blue"),
            ("Quiz termin\u00e9s", str(quiz_stats["quiz_count"]), "light-orange"),
            ("Score moyen", f'{quiz_stats["average_score"]}%', "light-purple"),
            ("Taux de r\u00e9ussite", f'{quiz_stats["pass_rate"]}%', "light-green"),
        ]

    quiz_attempts = [{**attempt, "id": str(attempt["_id"])} for attempt in quiz_stats["attempts"][:5]]
    source_quizzes = managed_quizzes if role in {"instructor", "admin"} else available_quizzes[:5]
    dashboard_quizzes = [{
        "id": str(item["_id"]), "title": item["title"],
        "description": item.get("description", ""),
        "question_count": len(item.get("questions", [])),
        "status": item.get("status", "draft"),
    } for item in source_quizzes]
    return render(request, "backoffice/dashboard.html", {
        "user": request.mongo_user,
        "role_label": {"admin": "Administrateur", "instructor": "Instructeur", "student": "\u00c9tudiant"}.get(role, role),
        "is_quiz_manager": role in {"instructor", "admin"},
        "stats": dashboard_stats,
        "quiz_stats": quiz_stats,
        "dashboard_quizzes": dashboard_quizzes,
        "quiz_attempts": quiz_attempts,
        "question_stats": quiz_stats["question_stats"],
        "quiz_storage_error": storage_error,
        "courses": [
            ("Python pour d\u00e9butants", "75%", "En cours"),
            ("D\u00e9veloppement web", "48%", "En cours"),
            ("Bases de donn\u00e9es", "100%", "Termin\u00e9"),
        ],
        "activity_heights": [35, 60, 45, 80, 55, 92, 70],
    })

def register(request):
    if request.session.get("user_id"):
        return redirect("users:profile")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = create_user(**{
                "first_name": form.cleaned_data["first_name"],
                "last_name": form.cleaned_data["last_name"],
                "email": form.cleaned_data["email"],
                "password": form.cleaned_data["password"],
            })
        except DuplicateEmailError:
            form.add_error("email", "Cette adresse e-mail est déjà utilisée.")
        except UserStorageError:
            messages.error(request, "Inscription impossible pour le moment.")
        else:
            request.session.flush()
            request.session["user_id"] = str(user["_id"])
            messages.success(request, "Votre compte a été créé.")
            return redirect("users:profile")
    return render(request, "users/register.html", {"form": form})


def login_view(request):
    if request.session.get("user_id"):
        return redirect("users:backoffice")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = authenticate_user(
                form.cleaned_data["email"], form.cleaned_data["password"]
            )
        except UserStorageError:
            messages.error(request, "Connexion impossible pour le moment.")
        else:
            if user:
                request.session.flush()
                request.session["user_id"] = str(user["_id"])
                messages.success(request, "Connexion réussie.")
                return redirect("users:backoffice")
            form.add_error(None, "Adresse e-mail ou mot de passe incorrect.")
    return render(request, "users/login.html", {"form": form})


def logout_view(request):
    if request.method == "POST":
        request.session.flush()
        messages.success(request, "Vous êtes déconnecté.")
    return redirect("users:login")


@user_required
def profile(request):
    form = ProfileForm(request.POST or None, initial={
        "first_name": request.mongo_user["first_name"],
        "last_name": request.mongo_user["last_name"],
    })
    if request.method == "POST" and form.is_valid():
        try:
            request.mongo_user = update_user_profile(
                request.session["user_id"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
            )
        except UserStorageError:
            messages.error(request, "Modification impossible pour le moment.")
        else:
            messages.success(request, "Profil mis à jour.")
            return redirect("users:profile")
    return render(request, "users/profile.html", {
        "form": form,
        "user": request.mongo_user,
    })
