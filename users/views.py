from django.contrib import messages
from django.shortcuts import redirect, render

from .decorators import user_required
from .forms import LoginForm, ProfileForm, RegistrationForm
from .services import (
    DuplicateEmailError,
    UserStorageError,
    authenticate_user,
    create_user,
    update_user_profile,
)


def home(request):
    return render(request, "frontoffice/index.html")


def courses(request):
    return render(request, "frontoffice/learning_page.html", {
        "page_title": "Cours",
        "page_intro": "Développez vos compétences avec des parcours conçus pour progresser à votre rythme.",
        "items": [
            ("Python pour débutants", "Apprenez les bases de Python avec des exercices pratiques.", "12 modules"),
            ("Développement web", "Construisez des interfaces modernes et accessibles.", "18 modules"),
            ("Bases de données", "Maîtrisez la conception et l'utilisation des bases de données.", "10 modules"),
        ],
        "item_label": "cours",
    })


def mentors(request):
    return render(request, "frontoffice/learning_page.html", {
        "page_title": "Mentors",
        "page_intro": "Échangez avec des experts qui vous accompagnent dans chaque étape de votre apprentissage.",
        "items": [
            ("Amine Benali", "Développement Python et architecture logicielle", "12 ans d'expérience"),
            ("Sofia Martin", "Design produit et expérience utilisateur", "9 ans d'expérience"),
            ("Lucas Bernard", "Data science et intelligence artificielle", "10 ans d'expérience"),
        ],
        "item_label": "mentor",
    })


def quiz(request):
    return render(request, "frontoffice/learning_page.html", {
        "page_title": "Quiz",
        "page_intro": "Testez vos connaissances et identifiez les compétences à renforcer.",
        "items": [
            ("Python - fondamentaux", "20 questions pour vérifier vos bases.", "Débutant"),
            ("HTML et CSS", "Évaluez votre maîtrise des interfaces web.", "Intermédiaire"),
            ("Logique de programmation", "Améliorez votre raisonnement avec des défis rapides.", "Tous niveaux"),
        ],
        "item_label": "quiz",
    })


@user_required
def backoffice(request):
    return render(request, "backoffice/dashboard.html", {
        "user": request.mongo_user,
        "stats": [
            ("Cours suivis", "12", "light-blue"),
            ("Progression", "68%", "light-orange"),
            ("Quiz réussis", "24", "light-purple"),
            ("Points gagnés", "1 280", "light-green"),
        ],
        "courses": [
            ("Python pour débutants", "75%", "En cours"),
            ("Développement web", "48%", "En cours"),
            ("Bases de données", "100%", "Terminé"),
        ],
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
    return _backoffice_section(
        request,
        "courses",
        "Dashboard des cours",
        "Gérez vos parcours et reprenez votre apprentissage.",
        [
            ("Cours disponibles", "24", "light-blue"),
            ("Cours en cours", "06", "light-orange"),
            ("Cours terminés", "12", "light-purple"),
            ("Progression moyenne", "68%", "light-green"),
        ],
        [
            ("Python pour débutants", "Programmation", "75%", "12 modules", "product-1.jpg"),
            ("Développement web", "Technologies web", "48%", "18 modules", "product-2.jpg"),
            ("Bases de données", "Data & backend", "100%", "10 modules", "product-3.jpg"),
        ],
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
        [
            ("Amine Benali", "Python & architecture logicielle", "Disponible", "12 ans d'expérience", "user-1.jpg"),
            ("Sofia Martin", "Design produit & UX", "Disponible", "9 ans d'expérience", "user-2.jpg"),
            ("Lucas Bernard", "Data science & IA", "Demain à 14h", "10 ans d'expérience", "user-3.jpg"),
        ],
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
        [
            ("Python - fondamentaux", "Programmation", "92%", "20 questions", "s1.jpg"),
            ("HTML et CSS", "Développement web", "84%", "15 questions", "s2.jpg"),
            ("Logique de programmation", "Compétences générales", "70%", "25 questions", "s3.jpg"),
        ],
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
