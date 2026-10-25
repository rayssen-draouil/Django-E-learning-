from django import forms

from apps.courses.catalog import COURSES
from .options import LEVEL_CHOICES, THEME_CHOICES

MAX_COURSES_PER_PATH = 10
COURSE_CHOICES = [(course["id"], course["title"]) for course in COURSES]


class LearningPathQuestionForm(forms.Form):
    question = forms.CharField(
        label="Votre question",
        max_length=500,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "placeholder": "Ex. Quelle étape m’apprend à créer une interface accessible ?",
        }),
    )


class LearningPathForm(forms.Form):
    title = forms.CharField(label="Titre", max_length=160)
    description = forms.CharField(
        label="Description",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    theme = forms.ChoiceField(label="Thème", choices=THEME_CHOICES)
    level = forms.ChoiceField(label="Niveau", choices=LEVEL_CHOICES)
    status = forms.ChoiceField(
        label="Statut",
        choices=(("draft", "Brouillon"), ("published", "Publié")),
    )

    def __init__(self, *args, **kwargs):
        learning_path = kwargs.pop("learning_path", None)
        super().__init__(*args, **kwargs)
        self.course_field_names = []
        for position in range(1, MAX_COURSES_PER_PATH + 1):
            field_name = f"course_{position}"
            self.course_field_names.append(field_name)
            self.fields[field_name] = forms.ChoiceField(
                label=f"Cours — étape {position}",
                choices=(("", "— Choisir un cours —"), *COURSE_CHOICES),
                required=False,
                help_text="Laissez vide pour terminer le parcours à cette étape.",
            )
        if learning_path and not self.is_bound:
            self.initial.update({
                "title": learning_path["title"],
                "description": learning_path.get("description", ""),
                "theme": learning_path.get("theme", "other"),
                "level": learning_path.get("level", "beginner"),
                "status": learning_path.get("status", "draft"),
            })
            for position, course_id in enumerate(learning_path.get("course_ids", []), start=1):
                if position <= MAX_COURSES_PER_PATH:
                    self.initial[f"course_{position}"] = course_id

    def clean(self):
        cleaned_data = super().clean()
        course_ids = [
            cleaned_data.get(field_name)
            for field_name in self.course_field_names
            if cleaned_data.get(field_name)
        ]
        if len(course_ids) < 2:
            raise forms.ValidationError("Un parcours doit contenir au moins deux cours.")
        if len(course_ids) != len(set(course_ids)):
            raise forms.ValidationError("Un même cours ne peut apparaître qu'une seule fois.")
        cleaned_data["course_ids"] = course_ids
        return cleaned_data

    def to_document(self) -> dict:
        return {
            "title": self.cleaned_data["title"].strip(),
            "description": self.cleaned_data["description"].strip(),
            "theme": self.cleaned_data["theme"],
            "level": self.cleaned_data["level"],
            "status": self.cleaned_data["status"],
            "course_ids": self.cleaned_data["course_ids"],
        }
