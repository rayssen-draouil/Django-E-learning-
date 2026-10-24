from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from apps.mentors.bookings import (
    STATUS_ACCEPTED,
    STATUS_COMPLETED,
    STATUS_PENDING,
    STATUS_RESCHEDULED_PENDING,
    list_student_bookings,
)
from apps.mentors.forms import MentorProfileSettingsForm
from apps.mentors.reviews import (
    get_mentor_rating_summary,
    get_review_by_booking,
    list_mentor_reviews,
    list_student_reviews,
)
from apps.mentors.services import (
    AvailabilityStorageError,
    get_mentor_availability_stats,
    list_mentor_slots,
)
from apps.quizzes.services import (
    QuizStorageError,
    get_student_quiz_stats,
    list_author_quizzes,
    list_published_quizzes,
)
from .decorators import user_required
from .forms import LoginForm, ProfileForm, RegistrationForm
from .services import (
    DuplicateEmailError,
    UserStorageError,
    authenticate_user,
    create_user,
    get_active_user,
    update_user_profile,
)


@user_required
def backoffice(request):
    role = request.mongo_user.get("role", "student")
    if role == "student":
        return HttpResponseForbidden("Students cannot access the back office. Please use your front-office learning space.")

    quiz_stats = {
        "attempts": [],
        "attempt_count": 0,
        "quiz_count": 0,
        "average_score": 0,
        "best_score": 0,
        "pass_rate": 0,
        "question_stats": [],
    }
    available_quizzes, managed_quizzes, storage_error = [], [], False
    mentor_stats = {
        "total_slots": 0,
        "upcoming_slots": 0,
        "online_slots": 0,
        "in_person_slots": 0,
        "booked_slots": 0,
    }
    mentor_slots = []
    try:
        quiz_stats = get_student_quiz_stats(request.mongo_user)
        available_quizzes = list_published_quizzes()
        if role in {"instructor", "admin"}:
            managed_quizzes = list_author_quizzes(request.mongo_user)
    except QuizStorageError:
        storage_error = True
        messages.error(
            request, "Les données Quiz sont temporairement indisponibles."
        )

    if role == "mentor":
        try:
            mentor_stats = get_mentor_availability_stats(request.mongo_user)
            mentor_slots = list_mentor_slots(request.mongo_user)[:5]
        except AvailabilityStorageError:
            messages.error(
                request,
                "Availability statistics are temporarily unavailable.",
            )

    if role == "mentor":
        dashboard_stats = [
            (
                "Total availability slots",
                str(mentor_stats["total_slots"]),
                "light-blue",
            ),
            (
                "Upcoming available slots",
                str(mentor_stats["upcoming_slots"]),
                "light-green",
            ),
            ("Online sessions", str(mentor_stats["online_slots"]), "light-purple"),
            (
                "In-person sessions",
                str(mentor_stats["in_person_slots"]),
                "light-orange",
            ),
        ]
    elif role in {"instructor", "admin"}:
        dashboard_stats = [
            ("Quiz gérés", str(len(managed_quizzes)), "light-blue"),
            (
                "Brouillons",
                str(sum(item.get("status") == "draft" for item in managed_quizzes)),
                "light-orange",
            ),
            (
                "Quiz publiés",
                str(
                    sum(item.get("status") == "published" for item in managed_quizzes)
                ),
                "light-purple",
            ),
            (
                "Tentatives personnelles",
                str(quiz_stats["attempt_count"]),
                "light-green",
            ),
        ]
    else:
        dashboard_stats = [
            ("Quiz disponibles", str(len(available_quizzes)), "light-blue"),
            ("Quiz terminés", str(quiz_stats["quiz_count"]), "light-orange"),
            ("Score moyen", f'{quiz_stats["average_score"]}%', "light-purple"),
            ("Taux de réussite", f'{quiz_stats["pass_rate"]}%', "light-green"),
        ]

    quiz_attempts = [
        {**attempt, "id": str(attempt["_id"])}
        for attempt in quiz_stats["attempts"][:5]
    ]
    source_quizzes = (
        managed_quizzes if role in {"instructor", "admin"} else available_quizzes[:5]
    )
    dashboard_quizzes = [
        {
            "id": str(item["_id"]),
            "title": item["title"],
            "description": item.get("description", ""),
            "question_count": len(item.get("questions", [])),
            "status": item.get("status", "draft"),
        }
        for item in source_quizzes
    ]
    return render(
        request,
        "backoffice/dashboard.html",
        {
            "user": request.mongo_user,
            "role_label": {
                "admin": "Administrateur",
                "instructor": "Instructeur",
                "mentor": "Mentor",
                "student": "Étudiant",
            }.get(role, role),
            "is_quiz_manager": role in {"instructor", "admin"},
            "is_mentor": role == "mentor",
            "mentor_stats": mentor_stats,
            "mentor_slots": mentor_slots,
            "stats": dashboard_stats,
            "quiz_stats": quiz_stats,
            "dashboard_quizzes": dashboard_quizzes,
            "quiz_attempts": quiz_attempts,
            "question_stats": quiz_stats["question_stats"],
            "quiz_storage_error": storage_error,
            "courses": [
                ("Python pour débutants", "75%", "En cours"),
                ("Développement web", "48%", "En cours"),
                ("Bases de données", "100%", "Terminé"),
            ],
            "activity_heights": [35, 60, 45, 80, 55, 92, 70],
        },
    )


def register(request):
    if request.session.get("user_id"):
        return redirect("users:profile")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            create_kwargs = {
                "first_name": form.cleaned_data["first_name"],
                "last_name": form.cleaned_data["last_name"],
                "email": form.cleaned_data["email"],
                "password": form.cleaned_data["password"],
            }
            selected_role = form.cleaned_data.get("role", "student")
            if selected_role and selected_role != "student":
                create_kwargs["role"] = selected_role
            user = create_user(**create_kwargs)
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
        try:
            user = get_active_user(request.session["user_id"])
            if user.get("role") == "student":
                return redirect("users:profile")
        except Exception:
            pass
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
                if user.get("role") == "student":
                    return redirect("users:profile")
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
    user = request.mongo_user
    role = user.get("role", "student")

    upcoming_sessions = []
    pending_sessions = []
    past_sessions = []
    student_reviews = []
    mentor_rating_summary = None
    mentor_reviews = []
    mentor_stats = None
    mentor_profile_form = None

    if role == "student":
        all_bookings = list_student_bookings(user["_id"])
        for b in all_bookings:
            status = b.get("status")
            if status == STATUS_ACCEPTED:
                upcoming_sessions.append(b)
            elif status in (STATUS_PENDING, STATUS_RESCHEDULED_PENDING):
                pending_sessions.append(b)
            else:
                if status == STATUS_COMPLETED:
                    b["review"] = get_review_by_booking(b["_id"])
                past_sessions.append(b)
        student_reviews = list_student_reviews(user["_id"])
    elif role == "mentor":
        mentor_rating_summary = get_mentor_rating_summary(user["_id"])
        mentor_reviews = list_mentor_reviews(user["_id"], limit=10)
        try:
            mentor_stats = get_mentor_availability_stats(user)
        except Exception:
            mentor_stats = None
        try:
            from config.mongodb import db
            full_doc = db["users"].find_one({"_id": user["_id"]}) or {}
            topics_val = full_doc.get("topics") or []
            if isinstance(topics_val, list):
                topics_val = ", ".join(str(t) for t in topics_val)
            langs_val = full_doc.get("languages") or []
            if isinstance(langs_val, list):
                langs_val = ", ".join(str(l) for l in langs_val)
            mentor_profile_form = MentorProfileSettingsForm(
                initial={
                    "experience": full_doc.get("experience", ""),
                    "bio": full_doc.get("bio", ""),
                    "topics": topics_val,
                    "languages": langs_val,
                    "teaching_style": full_doc.get("teaching_style", "beginner_friendly"),
                }
            )
        except Exception:
            mentor_profile_form = MentorProfileSettingsForm()

    form = ProfileForm(
        request.POST or None,
        initial={
            "first_name": user["first_name"],
            "last_name": user["last_name"],
        },
    )
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

    return render(
        request,
        "users/profile.html",
        {
            "form": form,
            "user": request.mongo_user,
            "role": role,
            "upcoming_sessions": upcoming_sessions,
            "pending_sessions": pending_sessions,
            "past_sessions": past_sessions,
            "student_reviews": student_reviews,
            "mentor_rating_summary": mentor_rating_summary,
            "mentor_reviews": mentor_reviews,
            "mentor_stats": mentor_stats,
            "mentor_profile_form": mentor_profile_form,
        },
    )
