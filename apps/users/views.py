from bson import ObjectId
from django.conf import settings
from django.contrib import messages
from django.core.mail import EmailMultiAlternatives
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse

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
from .decorators import roles_required, user_required
from .forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    ForgotPasswordForm,
    LoginForm,
    ProfileForm,
    RegistrationForm,
    ResetPasswordForm,
)
from .services import (
    ADMIN_ROLE_LABELS,
    AdminActionError,
    DuplicateEmailError,
    PasswordChangeError,
    UserNotFoundError,
    UserStorageError,
    admin_change_role,
    admin_create_account,
    admin_list_users,
    admin_set_active,
    authenticate_user,
    change_email,
    change_password,
    consume_password_reset,
    create_password_reset,
    create_user,
    get_active_user,
    is_password_reset_valid,
    update_user_profile,
)

RAPPEL_RESET = (
    "If an active account exists with this address, a reset link has been sent. "
    "Check your inbox (and spam folder)."
)


def _envoyer_lien_reset(email: str, lien: str) -> bool:
    """Envoie le lien via le relais SMTP configuré (Brevo/Console)."""
    context = {"lien": lien, "expire_minutes": 30}
    try:
        texte = render_to_string("users/emails/reset_password.txt", context)
        html = render_to_string("users/emails/reset_password.html", context)
        sender = getattr(settings, "EMAIL_SENDER", getattr(settings, "DEFAULT_FROM_EMAIL", "contact@skillforge.com"))
        message = EmailMultiAlternatives(
            subject="SkillForge Password Reset",
            body=texte,
            from_email=sender,
            to=[email],
        )
        message.attach_alternative(html, "text/html")
        message.send(fail_silently=False)
        return True
    except Exception as error:
        logging.getLogger(__name__).error("Failed to send reset email to %s: %s", email, error)
        return False


def _envoyer_lien_reset_email(request, user_id: str) -> None:
    """Génère un lien de reset pour le compte cible et l'envoie par e-mail."""
    from config.mongodb import db as _db

    document = _db.users.find_one({"_id": ObjectId(user_id)}, {"email": 1})
    if not document:
        raise UserNotFoundError
    token = create_password_reset(document["email"])
    if not token:
        raise UserNotFoundError
    lien = request.build_absolute_uri(reverse("users:reset_password", args=[token]))
    _envoyer_lien_reset(document["email"], lien)


@user_required
def backoffice(request):
    role = request.mongo_user.get("role", "student")

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

    support_stats = {"ticket_count": 0, "open": 0, "in_progress": 0, "resolved": 0, "unread": 0, "recent_tickets": []}
    try:
        from apps.support.services import list_for_user as _list_for_user, unread_notification_count as _unread
        _tickets = _list_for_user(request.mongo_user)
        support_stats["ticket_count"] = len(_tickets)
        for _item in _tickets:
            _status = _item.get("status", "open")
            if _status in support_stats:
                support_stats[_status] += 1
        support_stats["unread"] = _unread(request.mongo_user)
        support_stats["recent_tickets"] = _tickets[:5]
    except Exception:
        pass

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
        {**quiz, "id": str(quiz["_id"])} for quiz in source_quizzes
    ]
    activity_heights = [45, 70, 60, 85, 40, 95, 75]
    course_cards = [
        ("Fondamentaux Python", "80%", "En cours"),
        ("UI/UX Design", "55%", "En cours"),
        ("Bases MongoDB", "100%", "Terminé"),
    ]
    role_labels = {
        "admin": "Administrateur",
        "instructor": "Instructeur",
        "mentor": "Mentor",
        "student": "Apprenant",
    }
    return render(
        request,
        "backoffice/dashboard.html",
        {
            "user": request.mongo_user,
            "role": role,
            "role_label": role_labels.get(role, role.capitalize()),
            "is_mentor": role == "mentor",
            "is_quiz_manager": role in {"instructor", "admin"},
            "quiz_storage_error": storage_error,
            "stats": dashboard_stats,
            "quiz_stats": quiz_stats,
            "mentor_stats": mentor_stats,
            "mentor_slots": mentor_slots,
            "support_stats": support_stats,
            "activity_heights": activity_heights,
            "courses": course_cards,
            "quiz_attempts": quiz_attempts,
            "question_stats": quiz_stats["question_stats"][:5],
            "dashboard_quizzes": dashboard_quizzes,
        },
    )


def register(request):
    if request.session.get("user_id"):
        return redirect("users:profile")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = create_user(
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
                email=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
                role=form.cleaned_data.get("role", "student"),
            )
        except DuplicateEmailError:
            form.add_error("email", "This email address is already in use.")
        except UserStorageError:
            messages.error(request, "Registration is currently unavailable.")
        else:
            request.session["user_id"] = str(user["_id"])
            messages.success(request, "Registration successful.")
            return redirect("users:profile")
    return render(request, "users/register.html", {"form": form})


def forgot_password(request):
    if request.session.get("user_id"):
        return redirect("users:profile")
    form = ForgotPasswordForm(request.POST or None)
    dev_reset_link = None
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        try:
            token = create_password_reset(email)
        except UserStorageError:
            messages.error(request, "Unable to initiate password reset at this time.")
            return render(request, "users/forgot_password.html", {"form": ForgotPasswordForm(), "dev_reset_link": None})
        if token:
            lien = request.build_absolute_uri(reverse("users:reset_password", args=[token]))
            if getattr(settings, "EMAIL_HOST_USER", None):
                try:
                    _envoyer_lien_reset(email, lien)
                except Exception:
                    messages.error(request, "Failed to send email. Please try again in a few moments.")
            if settings.DEBUG:
                dev_reset_link = lien
        messages.success(request, RAPPEL_RESET)
        return render(request, "users/forgot_password.html", {"form": ForgotPasswordForm(), "dev_reset_link": dev_reset_link})
    return render(request, "users/forgot_password.html", {"form": form, "dev_reset_link": None})


def reset_password(request, token: str):
    if request.session.get("user_id"):
        return redirect("users:profile")
    try:
        jeton_valable = is_password_reset_valid(token)
    except UserStorageError:
        messages.error(request, "Unable to verify reset link at this time.")
        return redirect("users:forgot_password")
    if not jeton_valable:
        messages.error(request, "This reset link is invalid or expired. Please submit a new request.")
        return redirect("users:forgot_password")
    form = ResetPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            change = consume_password_reset(token, form.cleaned_data["password"])
        except UserStorageError:
            messages.error(request, "Unable to change password at this time.")
            return redirect("users:forgot_password")
        if not change:
            messages.error(request, "This reset link is invalid or expired. Please submit a new request.")
            return redirect("users:forgot_password")
        messages.success(request, "Your password has been changed. Please log in with your new password.")
        return redirect("users:login")
    return render(request, "users/reset_password.html", {"form": form, "token": token})


@roles_required("admin")
def admin_users(request):
    """Console de gestion des comptes (admin uniquement)."""
    section_actions = {
        "toggle_active": lambda target: admin_set_active(
            target, request.POST.get("become_active") == "1", request.mongo_user
        ),
        "change_role": lambda target: admin_change_role(target, request.POST.get("new_role", "student"), request.mongo_user),
        "create": lambda target: admin_create_account(
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
            email=request.POST.get("email", ""),
            password=request.POST.get("password", ""),
            role=request.POST.get("role", "student") if request.POST.get("role") in {"student", "instructor", "mentor", "admin"} else "student",
        ),
        "send_reset": lambda target: _envoyer_lien_reset_email(request, target),
    }
    if request.method == "POST":
        action = request.POST.get("action", "")
        traitement = section_actions.get(action)
        if traitement:
            try:
                traitement(request.POST.get("user_id", ""))
            except AdminActionError as erreur:
                messages.error(request, str(erreur))
            except DuplicateEmailError:
                messages.error(request, "Cette adresse e-mail possède déjà un compte.")
            except UserNotFoundError:
                messages.error(request, "Compte introuvable (peut-être vient-il d'être supprimé).")
            except Exception:
                messages.error(request, "Action impossible pour le moment (sur e-mail : vérifiez l'adresse).")
            else:
                messages.success(request, {
                    "toggle_active": "Statut du compte mis à jour.",
                    "change_role": "Rôle du compte mis à jour.",
                    "create": "Compte créé. Communiquez le mot de passe temporaire à l'intéressé.",
                    "send_reset": "Lien de réinitialisation envoyé par e-mail (valable 30 minutes).",
                }[action])
        return redirect(f"{reverse('users:admin_users')}?{request.GET.urlencode()}")
    search = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    active = request.GET.get("active", "")
    try:
        comptes_documents = admin_list_users(search=search, role=role, active=active)
    except UserStorageError:
        comptes_documents = []
        messages.error(request, "Service utilisateurs indisponible.")
    comptes = []
    for _doc in comptes_documents:
        _doc = dict(_doc)
        _doc["id"] = str(_doc["_id"])
        comptes.append(_doc)
    role_status = {
        "admin": ADMIN_ROLE_LABELS.get("admin", "Administrateur"),
        "instructor": ADMIN_ROLE_LABELS.get("instructor", "Instructeur"),
        "mentor": ADMIN_ROLE_LABELS.get("mentor", "Mentor"),
        "student": ADMIN_ROLE_LABELS.get("student", "Étudiant"),
    }
    return render(request, "users/admin_users.html", {
        "role_label": ADMIN_ROLE_LABELS.get("admin", "Administrateur"),
        "self_id": str(request.mongo_user["_id"]),
        "comptes": comptes,
        "total": len(comptes),
        "etudiants": sum(1 for _c in comptes if _c["role"] == "student"),
        "instructeurs": sum(1 for _c in comptes if _c["role"] == "instructor"),
        "mentors": sum(1 for _c in comptes if _c["role"] == "mentor"),
        "admins": sum(1 for _c in comptes if _c["role"] == "admin"),
        "actifs": sum(1 for _c in comptes if _c.get("is_active")),
        "filters": {"q": search, "role": role, "active": active},
        "role_status": role_status,
    })


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
            messages.error(request, "Login is currently unavailable.")
        else:
            if user:
                request.session.flush()
                request.session["user_id"] = str(user["_id"])
                messages.success(request, "Login successful.")
                next_url = request.GET.get("next") or request.POST.get("next")
                if next_url:
                    return redirect(next_url)
                return redirect("users:backoffice")
            form.add_error(None, "Invalid email address or password.")
    return render(request, "users/login.html", {"form": form})


def logout_view(request):
    if request.method == "POST":
        request.session.flush()
        messages.success(request, "You have been logged out.")
    return redirect("users:login")


@user_required
def profile(request):
    user = request.mongo_user
    role = user.get("role", "student")

    email_form = ChangeEmailForm(initial={"new_email": user.get("email", "")})
    password_form = ChangePasswordForm()

    upcoming_sessions = []
    pending_sessions = []
    past_sessions = []
    student_reviews = []
    mentor_rating_summary = None
    mentor_reviews = []
    mentor_stats = None
    mentor_profile_form = None

    if role == "student":
        try:
            all_bookings = list_student_bookings(user["_id"])
            for b in all_bookings:
                b_status = b.get("status")
                if b_status == STATUS_ACCEPTED:
                    upcoming_sessions.append(b)
                elif b_status in {STATUS_PENDING, STATUS_RESCHEDULED_PENDING}:
                    pending_sessions.append(b)
                elif b_status == STATUS_COMPLETED:
                    existing_rev = get_review_by_booking(b["_id"])
                    b["review"] = existing_rev
                    past_sessions.append(b)
            student_reviews = list_student_reviews(user["_id"])
        except Exception:
            pass
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

    action = request.POST.get("action", "") if request.method == "POST" else ""

    if request.method == "POST" and action == "email":
        email_form = ChangeEmailForm(request.POST)
        if email_form.is_valid():
            try:
                request.mongo_user = change_email(
                    request.session["user_id"],
                    current_password=email_form.cleaned_data["current_password"],
                    new_email=email_form.cleaned_data["new_email"],
                )
            except PasswordChangeError:
                messages.error(request, "Le mot de passe actuel est incorrect.")
            except DuplicateEmailError:
                messages.error(request, "Cette adresse e-mail est déjà utilisée.")
            except UserStorageError:
                messages.error(request, "Modification impossible pour le moment.")
            else:
                messages.success(request, "Votre adresse e-mail a été mise à jour.")
                return redirect("users:profile")
    elif request.method == "POST" and action == "password":
        password_form = ChangePasswordForm(request.POST)
        if password_form.is_valid():
            try:
                change_password(
                    request.session["user_id"],
                    current_password=password_form.cleaned_data["current_password"],
                    new_password=password_form.cleaned_data["new_password"],
                )
            except PasswordChangeError:
                messages.error(request, "Le mot de passe actuel est incorrect.")
            except UserStorageError:
                messages.error(request, "Modification impossible pour le moment.")
            else:
                messages.success(request, "Succès : votre mot de passe a été mis à jour.")
                return redirect("users:profile")
    elif request.method == "POST" and action in {"", "profile"} and form.is_valid():
        try:
            request.mongo_user = update_user_profile(
                request.session["user_id"],
                first_name=form.cleaned_data["first_name"],
                last_name=form.cleaned_data["last_name"],
            )
        except UserStorageError:
            messages.error(request, "Mise à jour impossible pour le moment.")
        except UserNotFoundError:
            messages.error(request, "Utilisateur introuvable.")
        else:
            messages.success(request, "Profil mis à jour.")
            return redirect("users:profile")

    membership = request.mongo_user.get("created_at")
    member_since = membership.strftime("%d/%m/%Y") if hasattr(membership, "strftime") else ""
    quiz_stats = {"attempt_count": 0, "average_score": 0.0, "best_score": 0.0, "pass_rate": 0.0}
    support_stats = {"ticket_count": 0, "open": 0}
    if role == "student":
        try:
            quiz_stats = get_student_quiz_stats(request.mongo_user)
        except QuizStorageError:
            pass
    try:
        from apps.support.services import list_for_user as _list_for_user
        _tickets = _list_for_user(request.mongo_user)
        support_stats["ticket_count"] = len(_tickets)
        support_stats["open"] = sum(1 for _item in _tickets if _item.get("status", "open") != "resolved")
    except Exception:
        pass
    managed_quizzes_count = None
    if role in {"instructor", "admin"}:
        try:
            managed_quizzes_count = len(list_author_quizzes(request.mongo_user))
        except QuizStorageError:
            pass

    role_labels = {
        "admin": "Administrateur",
        "instructor": "Instructeur",
        "mentor": "Mentor",
        "student": "Étudiant",
    }

    return render(
        request,
        "users/profile.html",
        {
            "form": form,
            "email_form": email_form,
            "password_form": password_form,
            "user": request.mongo_user,
            "role": role,
            "role_label": role_labels.get(role, role.capitalize()),
            "member_since": member_since,
            "quiz_stats": quiz_stats,
            "support_stats": support_stats,
            "managed_quizzes_count": managed_quizzes_count,
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
