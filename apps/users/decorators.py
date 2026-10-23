from functools import wraps
from typing import Any, Callable

from django.contrib import messages
from django.http import HttpResponseForbidden
from django.shortcuts import redirect

from apps.users.services import UserNotFoundError, UserStorageError, get_active_user


def user_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(request: Any, *args: Any, **kwargs: Any) -> Any:
        user_id = request.session.get("user_id")
        if not user_id:
            messages.info(request, "Connectez-vous pour accéder à cette page.")
            return redirect("users:login")
        try:
            request.mongo_user = get_active_user(user_id)
        except UserNotFoundError:
            request.session.flush()
            messages.error(request, "Votre session n'est plus valide.")
            return redirect("users:login")
        except UserStorageError:
            messages.error(request, "Le service utilisateurs est indisponible.")
            return redirect("users:login")
        return view(request, *args, **kwargs)

    return wrapped


def roles_required(*allowed_roles: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Require an active MongoDB account with one of the specified business roles."""
    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)
        def check_role(request: Any, *args: Any, **kwargs: Any) -> Any:
            if request.mongo_user.get("role") not in allowed_roles:
                return HttpResponseForbidden("Accès refusé.")
            return view(request, *args, **kwargs)

        return user_required(check_role)

    return decorator
