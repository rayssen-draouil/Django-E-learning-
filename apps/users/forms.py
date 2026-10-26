from django import forms
from django.contrib.auth.password_validation import validate_password


REGISTRATION_ROLE_CHOICES = (
    ("student", "Student"),
    ("mentor", "Mentor"),
    ("instructor", "Instructor"),
)


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField(label="Email address")


class ResetPasswordForm(forms.Form):
    password = forms.CharField(label="New password", widget=forms.PasswordInput)
    password_confirm = forms.CharField(
        label="Confirm new password", widget=forms.PasswordInput
    )

    def clean(self) -> dict[str, str]:
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirmation = cleaned_data.get("password_confirm")
        if password and confirmation and password != confirmation:
            self.add_error("password_confirm", "Passwords do not match.")
        if password:
            validate_password(password)
        return cleaned_data


class RegistrationForm(forms.Form):
    ROLE_CHOICES = REGISTRATION_ROLE_CHOICES

    first_name = forms.CharField(label="First name", max_length=100)
    last_name = forms.CharField(label="Last name", max_length=100)
    email = forms.EmailField(label="Email address")
    role = forms.ChoiceField(
        label="Account type",
        choices=REGISTRATION_ROLE_CHOICES,
        required=False,
        initial="student",
        widget=forms.Select(attrs={"class": "auth-select", "id": "id_role"}),
        help_text="Instructors and mentors manage their sessions, courses, and quizzes; administrator accounts are created by the team.",
    )
    password = forms.CharField(label="Password", widget=forms.PasswordInput)
    password_confirm = forms.CharField(
        label="Confirm password", widget=forms.PasswordInput
    )

    def clean_email(self) -> str:
        return self.cleaned_data["email"].strip().lower()

    def clean_role(self) -> str:
        value = self.cleaned_data.get("role")
        return value if value else "student"

    def clean(self) -> dict[str, str]:
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirmation = cleaned_data.get("password_confirm")
        if password and confirmation and password != confirmation:
            self.add_error("password_confirm", "Passwords do not match.")
        if password:
            validate_password(password)
        return cleaned_data


class LoginForm(forms.Form):
    email = forms.EmailField(label="Email address")
    password = forms.CharField(label="Password", widget=forms.PasswordInput)

    def clean_email(self) -> str:
        return self.cleaned_data["email"].strip().lower()


class ProfileForm(forms.Form):
    first_name = forms.CharField(label="First name", max_length=100)
    last_name = forms.CharField(label="Last name", max_length=100)


class ChangeEmailForm(forms.Form):
    current_password = forms.CharField(label="Current password", widget=forms.PasswordInput)
    new_email = forms.EmailField(label="New email address")
    new_email_confirm = forms.EmailField(label="Confirm new email address")

    def clean_new_email(self) -> str:
        return self.cleaned_data["new_email"].strip().lower()

    def clean_new_email_confirm(self) -> str:
        return self.cleaned_data["new_email_confirm"].strip().lower()

    def clean(self) -> dict[str, str]:
        cleaned_data = super().clean()
        new_email = cleaned_data.get("new_email")
        confirmation = cleaned_data.get("new_email_confirm")
        if new_email and confirmation and new_email != confirmation:
            self.add_error("new_email_confirm", "Email addresses do not match.")
        return cleaned_data


class ChangePasswordForm(forms.Form):
    current_password = forms.CharField(label="Current password", widget=forms.PasswordInput)
    new_password = forms.CharField(label="New password", widget=forms.PasswordInput)
    new_password_confirm = forms.CharField(
        label="Confirm new password", widget=forms.PasswordInput
    )

    def clean(self) -> dict[str, str]:
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirmation = cleaned_data.get("new_password_confirm")
        if new_password and confirmation and new_password != confirmation:
            self.add_error("new_password_confirm", "Passwords do not match.")
        if new_password:
            validate_password(new_password)
        return cleaned_data
