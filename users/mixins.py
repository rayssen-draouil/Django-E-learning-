"""Reusable permission mixins built on Django's auth mixins."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse

from .permissions import can_edit, is_editor


class ErrorRedirectMixin:
    """Redirect unauthorized users to a friendly page instead of a bare 403."""

    error_url_name = "home"

    def get_error_url(self):
        kwargs = {
            key: self.kwargs[key]
            for key in ("pk", "quiz_id", "slug")
            if key in self.kwargs
        }
        try:
            return reverse(self.error_url_name, kwargs=kwargs)
        except NoReverseMatch:
            return "/"

    def handle_no_permission(self):
        if not self.request.user.is_authenticated:
            messages.info(self.request, "Connectez-vous pour accéder à cette page.")
            return super().handle_no_permission()
        messages.error(
            self.request,
            "Vous n'avez pas les droits nécessaires pour effectuer cette action.",
        )
        return redirect(self.get_error_url())


class EditorRequiredMixin(LoginRequiredMixin, ErrorRedirectMixin, UserPassesTestMixin):
    """Allow content creation to staff members and mentors only."""

    def test_func(self):
        return is_editor(self.request.user)


class OwnerOrStaffMixin(LoginRequiredMixin, ErrorRedirectMixin, UserPassesTestMixin):
    """Allow edition only to staff members and to the content owner."""

    def test_func(self):
        return can_edit(self.request.user, self.get_object())
