from django.contrib import messages
from bson import ObjectId
from django.conf import settings
from django.shortcuts import redirect, render
from django.urls import reverse

from apps.quizzes.services import QuizStorageError, get_student_quiz_stats, list_author_quizzes, list_published_quizzes
from .decorators import roles_required, user_required
from .forms import ChangeEmailForm, ChangePasswordForm, ForgotPasswordForm, LoginForm, ProfileForm, RegistrationForm, ResetPasswordForm
from .services import (
    AdminActionError,
    DuplicateEmailError,
    UserNotFoundError,
    UserStorageError,
    ADMIN_ROLE_LABELS,
    admin_change_role,
    admin_create_account,
    admin_list_users,
    admin_set_active,
    authenticate_user,
    consume_password_reset,
    create_password_reset,
    change_password,
    create_user,
    is_password_reset_valid,
    update_user_profile,
    change_email,
)
from .services import PasswordChangeError


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

    # Mini-résumé Support pour le tableau de bord (échoue en douceur si le
    # service support est indisponible : le dashboard reste affichable).
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
    except Exception:
        _tickets = []

    # Admin : aperçu des dernières demandes reçues directement sur le dashboard.
    if role == "admin":
        _status_labels = {"open": "Ouvert", "in_progress": "En cours", "waiting": "En attente", "resolved": "Résolu"}
        _priority_labels = {"low": "Faible", "normal": "Normale", "high": "Élevée", "urgent": "Urgente"}
        for _item in list(_tickets)[:5]:
            support_stats["recent_tickets"].append({
                "id": str(_item.get("_id")),
                "title": _item.get("title") or "(sans titre)",
                "status_label": _status_labels.get(_item.get("status", "open"), _item.get("status", "open")),
                "priority_label": _priority_labels.get(_item.get("priority", "normal"), _item.get("priority", "")),
                "updated_at": _item.get("updated_at"),
            })

    if role in {"instructor", "admin"}:
        dashboard_stats = [
            ("✍", "Quiz g\u00e9r\u00e9s", str(len(managed_quizzes)), "light-blue"),
            ("📝", "Brouillons", str(sum(item.get("status") == "draft" for item in managed_quizzes)), "light-orange"),
            ("🚀", "Quiz publi\u00e9s", str(sum(item.get("status") == "published" for item in managed_quizzes)), "light-purple"),
            ("✉", "Demandes support", str(support_stats["ticket_count"]), "light-green"),
        ]
    else:
        dashboard_stats = [
            ("📚", "Quiz disponibles", str(len(available_quizzes)), "light-blue"),
            ("✅", "Quiz termin\u00e9s", str(quiz_stats["quiz_count"]), "light-orange"),
            ("🎯", "Score moyen", f'{quiz_stats["average_score"]}%', "light-purple"),
            ("🏆", "Taux de r\u00e9ussite", f'{quiz_stats["pass_rate"]}%', "light-green"),
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
        "support_stats": support_stats,
        "courses": [
            ("Python pour d\u00e9butants", "75%", "En cours"),
            ("D\u00e9veloppement web", "48%", "En cours"),
            ("Bases de donn\u00e9es", "100%", "Termin\u00e9"),
        ],
        "activity_heights": [35, 60, 45, 80, 55, 92, 70],
    })

def register(request):
    if request.session.get("user_id"):
        return redirect("home")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            user = create_user(**{
                "first_name": form.cleaned_data["first_name"],
                "last_name": form.cleaned_data["last_name"],
                "email": form.cleaned_data["email"],
                "password": form.cleaned_data["password"],
                "role": form.cleaned_data.get("role", "student"),
            })
        except DuplicateEmailError:
            form.add_error("email", "Cette adresse e-mail est déjà utilisée.")
        except UserStorageError:
            messages.error(request, "Inscription impossible pour le moment.")
        else:
            request.session.flush()
            request.session["user_id"] = str(user["_id"])
            messages.success(request, "Votre compte a été créé.")
            return redirect("home")
    return render(request, "users/register.html", {"form": form})


RAPPEL_RESET = (
    "Si un compte existe pour cette adresse, un lien de réinitialisation valable 30 minutes vient d'être envoyé."
)


def _envoyer_lien_reset(email: str, lien: str) -> None:
    """Envoie le lien via le relais SMTP configuré ( Brevo ). Lève en cas d'échec."""
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    context = {"lien": lien, "expire_minutes": 30}
    texte = render_to_string("users/emails/reset_password.txt", context)
    html = render_to_string("users/emails/reset_password.html", context)
    message = EmailMultiAlternatives(
        subject="Réinitialisation de votre mot de passe SkillForge",
        body=texte,
        from_email=settings.EMAIL_SENDER,
        to=[email],
    )
    message.attach_alternative(html, "text/html")
    message.send()


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
            messages.error(request, "Impossible de lancer la réinitialisation pour le moment.")
            return render(request, "users/forgot_password.html", {"form": ForgotPasswordForm(), "dev_reset_link": None})
        if token:
            lien = request.build_absolute_uri(reverse("users:reset_password", args=[token]))
            if settings.EMAIL_HOST_USER:
                emails_invalides = False
                try:
                    _envoyer_lien_reset(email, lien)
                except Exception:
                    emails_invalides = True
                    messages.error(request, "L'envoi de l'e-mail a échoué. Réessayez dans quelques instants.")
                if emails_invalides and settings.DEBUG:
                    # filet de développement : le lien reste utilisable sans e-mail
                    dev_reset_link = lien
            elif settings.DEBUG:
                # pas de relais configuré : lien direct en développement uniquement
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
        messages.error(request, "Impossible de vérifier le lien pour le moment.")
        return redirect("users:forgot_password")
    if not jeton_valable:
        messages.error(request, "Ce lien de réinitialisation est invalide ou expiré. Refaites la demande.")
        return redirect("users:forgot_password")
    form = ResetPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            change = consume_password_reset(token, form.cleaned_data["password"])
        except UserStorageError:
            messages.error(request, "Impossible de changer le mot de passe pour le moment.")
            return redirect("users:forgot_password")
        if not change:
            messages.error(request, "Ce lien de réinitialisation est invalide ou expiré. Refaites la demande.")
            return redirect("users:forgot_password")
        messages.success(request, "Votre mot de passe a été changé. Connectez-vous avec le nouveau.")
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
            role="instructor" if request.POST.get("role") != "student" else "student",
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
        "admin": ADMIN_ROLE_LABELS["admin"],
        "instructor": ADMIN_ROLE_LABELS["instructor"],
        "student": ADMIN_ROLE_LABELS["student"],
    }
    return render(request, "users/admin_users.html", {
        "role_label": ADMIN_ROLE_LABELS["admin"],
        "self_id": str(request.mongo_user["_id"]),
        "comptes": comptes,
        "total": len(comptes),
        "etudiants": sum(1 for _c in comptes if _c["role"] == "student"),
        "instructeurs": sum(1 for _c in comptes if _c["role"] == "instructor"),
        "admins": sum(1 for _c in comptes if _c["role"] == "admin"),
        "actifs": sum(1 for _c in comptes if _c.get("is_active")),
        "filters": {"q": search, "role": role, "active": active},
        "role_status": role_status,
    })


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


def login_view(request):
    if request.session.get("user_id"):
        return redirect("home")
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
                return redirect("home")
            form.add_error(None, "Adresse e-mail ou mot de passe incorrect.")
    return render(request, "users/login.html", {"form": form})


def logout_view(request):
    if request.method == "POST":
        request.session.flush()
        messages.success(request, "Vous êtes déconnecté.")
    return redirect("users:login")


@user_required
def profile(request):
    action = request.POST.get("action", "") if request.method == "POST" else ""
    profile_data = request.POST if request.method == "POST" and action in {"", "profile"} else None
    form = ProfileForm(profile_data, initial={
        "first_name": request.mongo_user["first_name"],
        "last_name": request.mongo_user["last_name"],
    })
    email_form = ChangeEmailForm()
    password_form = ChangePasswordForm()
    if request.method == "POST" and action == "email":
        email_form = ChangeEmailForm(request.POST)
        if email_form.is_valid():
            try:
                change_email(
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
            messages.error(request, "Modification impossible pour le moment.")
        else:
            messages.success(request, "Profil mis à jour.")
            return redirect("users:profile")
    role = request.mongo_user.get("role", "student")
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
    return render(request, "users/profile.html", {
        "form": form,
        "email_form": email_form,
        "password_form": password_form,
        "user": request.mongo_user,
        "role_label": {"admin": "Administrateur", "instructor": "Instructeur", "student": "Étudiant"}.get(role, role),
        "member_since": member_since,
        "quiz_stats": quiz_stats,
        "support_stats": support_stats,
        "managed_quizzes_count": managed_quizzes_count,
    })
