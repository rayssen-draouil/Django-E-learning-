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
    if request.session.get("user_id"):
        return redirect("users:profile")
    return redirect("users:login")


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
        return redirect("users:profile")
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
                return redirect("users:profile")
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
