from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.db.models import Count
from django.shortcuts import redirect, render

from .decorators import user_required
from .forms import LoginForm, ProfileForm, RegistrationForm
from .models import Course
from .permissions import visible_courses
from .services import (
    DuplicateEmailError,
    UserStorageError,
    authenticate_user,
    create_user,
    update_user_profile,
)

DJANGO_AUTH_BACKEND = "django.contrib.auth.backends.ModelBackend"


def _sync_django_user(mongo_user):
    """Mirror a MongoDB account into Django's auth tables.

    MongoDB remains the source of truth for credentials; the mirrored account
    only exists so that ``request.user`` is a real user and the permission
    mixins (``LoginRequiredMixin`` / ``UserPassesTestMixin``) can work.
    """
    user_model = get_user_model()
    email = mongo_user["email"]
    first_name = mongo_user.get("first_name", "")
    last_name = mongo_user.get("last_name", "")
    user, created = user_model.objects.get_or_create(
        email=email,
        defaults={
            "username": email,
            "first_name": first_name,
            "last_name": last_name,
        },
    )
    updates = {}
    if not created:
        if user.first_name != first_name:
            updates["first_name"] = first_name
        if user.last_name != last_name:
            updates["last_name"] = last_name
        if mongo_user.get("role") == "admin" and not user.is_staff:
            updates["is_staff"] = True
        if updates:
            user_model.objects.filter(pk=user.pk).update(**updates)
            for field, value in updates.items():
                setattr(user, field, value)
    return user


def home(request):
    return render(request, "frontoffice/index.html")


def page_not_found(request, exception=None):
    return render(request, "404.html", status=404)


def _visible_courses_with_modules(request):
    return (
        visible_courses(request.user)
        .select_related("mentor__user")
        .annotate(annotated_module_count=Count("modules", distinct=True))
        .order_by("-created_at")
    )


@user_required
def backoffice(request):
    courses = _visible_courses_with_modules(request)[:3]
    return render(request, "backoffice/dashboard.html", {
        "user": request.mongo_user,
        "stats": [
            ("Cours suivis", "12", "light-blue"),
            ("Progression", "68%", "light-orange"),
            ("Quiz réussis", "24", "light-purple"),
            ("Points gagnés", "1 280", "light-green"),
        ],
        "courses": courses,
        "activity_heights": [35, 60, 45, 80, 55, 92, 70],
    })


def _backoffice_section(request, section, title, intro, stats, items):
    return render(request, "backoffice/section_dashboard.html", {
        "user": request.mongo_user,
        "section": section,
        "title": title,
        "intro": intro,
        "stats": stats,
        "items": items,
    })


@user_required
def backoffice_courses(request):
    courses = list(_visible_courses_with_modules(request))
    return _backoffice_section(
        request,
        "courses",
        "Dashboard des cours",
        "Parcourez les cours de la plateforme et reprenez votre apprentissage.",
        [
            ("Cours disponibles", str(len(courses)), "light-blue"),
            ("Modules au total", str(sum(c.module_count for c in courses)), "light-orange"),
            ("Cours avec mentor", str(sum(1 for c in courses if c.mentor_id)), "light-purple"),
            ("Niveaux couverts", str(len({c.level for c in courses})), "light-green"),
        ],
        courses,
    )


@user_required
def backoffice_mentors(request):
    return _backoffice_section(
        request,
        "mentors",
        "Dashboard des mentors",
        "Trouvez le bon accompagnement pour atteindre vos objectifs.",
        [
            ("Mentors disponibles", "18", "light-blue"),
            ("Sessions réalisées", "32", "light-orange"),
            ("Mentor préféré", "4.9/5", "light-purple"),
            ("Heures de coaching", "46h", "light-green"),
        ],
        [],
    )


@user_required
def backoffice_quizzes(request):
    return _backoffice_section(
        request,
        "quizzes",
        "Dashboard des quiz",
        "Mesurez vos connaissances et identifiez vos axes de progression.",
        [
            ("Quiz disponibles", "36", "light-blue"),
            ("Quiz réalisés", "24", "light-orange"),
            ("Taux de réussite", "82%", "light-purple"),
            ("Meilleur score", "96%", "light-green"),
        ],
        [],
    )


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
            django_login(request, _sync_django_user(user), backend=DJANGO_AUTH_BACKEND)
            messages.success(request, "Votre compte a été créé.")
            return redirect("users:profile")
    return render(request, "users/register.html", {"form": form})


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
                django_login(request, _sync_django_user(user), backend=DJANGO_AUTH_BACKEND)
                messages.success(request, "Connexion réussie.")
                return redirect("home")
            form.add_error(None, "Adresse e-mail ou mot de passe incorrect.")
    return render(request, "users/login.html", {"form": form})


def logout_view(request):
    if request.method == "POST":
        request.session.flush()
        django_logout(request)
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
