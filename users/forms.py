import os

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.forms import inlineformset_factory
from django.shortcuts import redirect
from PIL import Image, UnidentifiedImageError

from .models import Answer, Category, Course, Mentor, Module, Question, Quiz
from .permissions import mentors_queryset, owned_courses

User = get_user_model()


class RegistrationForm(forms.Form):
    first_name = forms.CharField(label="Prénom", max_length=100)
    last_name = forms.CharField(label="Nom", max_length=100)
    email = forms.EmailField(label="Adresse e-mail")
    password = forms.CharField(label="Mot de passe", widget=forms.PasswordInput)
    password_confirm = forms.CharField(
        label="Confirmation du mot de passe", widget=forms.PasswordInput
    )

    def clean_email(self) -> str:
        return self.cleaned_data["email"].strip().lower()

    def clean(self) -> dict[str, str]:
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        confirmation = cleaned_data.get("password_confirm")
        if password and confirmation and password != confirmation:
            self.add_error("password_confirm", "Les mots de passe ne correspondent pas.")
        if password:
            validate_password(password)
        return cleaned_data


class LoginForm(forms.Form):
    email = forms.EmailField(label="Adresse e-mail")
    password = forms.CharField(label="Mot de passe", widget=forms.PasswordInput)

    def clean_email(self) -> str:
        return self.cleaned_data["email"].strip().lower()


class ProfileForm(forms.Form):
    first_name = forms.CharField(label="Prénom", max_length=100)
    last_name = forms.CharField(label="Nom", max_length=100)

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_SIZE = 2 * 1024 * 1024  # 2 MB
IMAGE_ERROR = "Formats acceptés : JPEG, PNG ou WEBP, 2 Mo maximum."


def validate_image_file(image: UploadedFile) -> None:
    """Check the extension, size and integrity of an uploaded image."""
    if not image:
        return
    extension = os.path.splitext(image.name)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(IMAGE_ERROR)
    content_type = getattr(image, "content_type", "")
    if content_type and content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise ValidationError(IMAGE_ERROR)
    if image.size > MAX_IMAGE_SIZE:
        raise ValidationError("L'image ne doit pas dépasser 2 Mo.")
    try:
        image.seek(0)
        Image.open(image).verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValidationError("Ce fichier n'est pas une image valide.") from error
    finally:
        image.seek(0)


class FrenchFormMixin:
    """Add responsive CSS classes to rendered widgets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, (forms.Textarea,)):
                widget.attrs.setdefault("rows", 5)
            if isinstance(widget, (forms.Select,)):
                widget.attrs.setdefault("class", "form-control")
            widget.attrs.setdefault("placeholder", str(field.label or ""))


class CourseForm(FrenchFormMixin, forms.ModelForm):
    class Meta:
        model = Course
        fields = [
            "title",
            "slug",
            "short_description",
            "description",
            "level",
            "category",
            "duration_hours",
            "image",
        ]
        widgets = {
            "short_description": forms.Textarea(attrs={"rows": 3}),
            "description": forms.Textarea(attrs={"rows": 6}),
            "image": forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = Category.objects.order_by("name")
        self.fields["category"].required = True
        self.fields["category"].empty_label = "— Choisir une catégorie —"

    def clean_image(self):
        image = self.cleaned_data.get("image")
        validate_image_file(image)
        return image


class ModuleForm(FrenchFormMixin, forms.ModelForm):
    class Meta:
        model = Module
        fields = ["course", "title", "content", "order"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 8}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = owned_courses(user) if user else Course.objects.none()
        self.fields["course"].empty_label = "— Choisir un cours —"


class MentorForm(FrenchFormMixin, forms.ModelForm):
    class Meta:
        model = Mentor
        fields = ["user", "bio", "specialties", "photo"]
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 6}),
            "user": forms.HiddenInput,
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_user = user
        self.fields["user"].required = False
        if user and user.is_staff:
            self.fields["user"].queryset = User.objects.order_by("email")
            self.fields["user"].widget = forms.Select()
        elif user:
            self.fields["user"].queryset = User.objects.filter(pk=user.pk)
        else:
            self.fields["user"].queryset = User.objects.none()
        self.fields["bio"].required = True

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        validate_image_file(photo)
        return photo

    def clean(self):
        cleaned_data = super().clean()
        user = cleaned_data.get("user")
        is_staff = bool(self.current_user and self.current_user.is_staff)
        if not user and self.instance.pk:
            user = self.instance.user
            cleaned_data["user"] = user
        if not user and is_staff:
            self.add_error("user", "Sélectionnez un utilisateur.")
        if user and not self.instance.pk and Mentor.objects.filter(user=user).exists():
            self.add_error("user", "Cet utilisateur possède déjà un profil mentor.")
        return cleaned_data


class QuizForm(FrenchFormMixin, forms.ModelForm):
    class Meta:
        model = Quiz
        fields = ["title", "description", "course", "is_published"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["course"].queryset = owned_courses(user) if user else Course.objects.none()
        self.fields["course"].empty_label = "— Choisir un cours —"


class QuestionForm(FrenchFormMixin, forms.ModelForm):
    class Meta:
        model = Question
        fields = ["text", "score", "order"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 4}),
        }


class AnswerForm(FrenchFormMixin, forms.ModelForm):
    class Meta:
        model = Answer
        fields = ["text", "is_correct"]


class BaseAnswerFormSet(forms.BaseInlineFormSet):
    """Require at least one answer and exactly one correct answer."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        answers = [
            cleaned for cleaned in self.cleaned_data
            if cleaned and not cleaned.get("DELETE") and cleaned.get("text")
        ]
        correct = [cleaned for cleaned in answers if cleaned.get("is_correct")]
        if len(correct) != 1:
            raise ValidationError("Cochez exactement une bonne réponse.")


AnswerFormSet = inlineformset_factory(
    Question,
    Answer,
    form=AnswerForm,
    formset=BaseAnswerFormSet,
    extra=4,
    min_num=1,
    validate_min=True,
    can_delete=True,
    labels={
        "text": "Réponse",
        "is_correct": "Bonne réponse",
        "DELETE": "Supprimer",
    },
)


class QuestionFormSetMixin:
    """Render and validate a question together with its answers."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "answer_formset" not in context:
            context["answer_formset"] = self.get_answer_formset()
        return context

    def get_answer_formset(self, data=None, files=None):
        instance = getattr(self, "object", None)
        if instance is None or instance.pk is None:
            instance = Question()
        return AnswerFormSet(data, files, instance=instance, prefix="answers")

    def form_valid(self, form):
        answer_formset = self.get_answer_formset(
            self.request.POST, self.request.FILES
        )
        if not answer_formset.is_valid():
            return self.render_to_response(
                self.get_context_data(form=form, answer_formset=answer_formset)
            )
        self.object = form.save()
        answer_formset.instance = self.object
        answer_formset.save()
        return redirect(self.object.quiz.get_absolute_url())
