from __future__ import annotations

from decimal import Decimal

from django import forms
from django.utils.text import slugify
from pymongo.errors import PyMongoError

from . import repository

CATEGORY_CHOICES = (
    ("programming", "Programmation"),
    ("web", "Développement web"),
    ("data", "Données & IA"),
    ("design", "Design"),
    ("business", "Business"),
    ("other", "Autre"),
)

LEVEL_CHOICES = (
    ("beginner", "Débutant"),
    ("intermediate", "Intermédiaire"),
    ("advanced", "Avancé"),
)

STATUS_CHOICES = (
    ("draft", "Brouillon"),
    ("published", "Publié"),
)

CATEGORY_LABELS = dict(CATEGORY_CHOICES)
LEVEL_LABELS = dict(LEVEL_CHOICES)


class CourseForm(forms.Form):
    title = forms.CharField(
        label="Titre",
        max_length=160,
        widget=forms.TextInput(attrs={"autocomplete": "off", "minlength": 3}),
    )
    slug = forms.CharField(
        label="Identifiant d'URL (slug)",
        max_length=180,
        required=False,
        help_text="Laissez vide pour générer automatiquement un identifiant à partir du titre.",
    )
    description = forms.CharField(
        label="Description",
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 8}),
    )
    objectives = forms.CharField(
        label="Objectifs d'apprentissage",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text="Un objectif par ligne. L'assistant IA en propose cinq.",
    )
    category = forms.ChoiceField(label="Catégorie", choices=CATEGORY_CHOICES)
    level = forms.ChoiceField(label="Niveau", choices=LEVEL_CHOICES)
    duration_hours = forms.DecimalField(
        label="Durée (heures)",
        max_digits=6,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        initial=0,
        widget=forms.NumberInput(attrs={"min": "0", "step": "0.25", "inputmode": "decimal"}),
    )
    language = forms.CharField(
        label="Langue",
        max_length=50,
        required=False,
        initial="fr",
        widget=forms.TextInput(attrs={"maxlength": 50}),
    )
    is_free = forms.BooleanField(label="Cours gratuit", required=False)
    price = forms.DecimalField(
        label="Prix (€)",
        max_digits=8,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        widget=forms.NumberInput(attrs={"min": "0", "step": "0.01", "inputmode": "decimal"}),
        help_text="Obligatoire pour un cours payant. Ignoré pour un cours gratuit.",
    )
    thumbnail = forms.URLField(
        label="Image de couverture (URL)",
        max_length=500,
        required=False,
        help_text="Adresse d'une image affichée sur la fiche du cours. Facultatif.",
    )
    thumbnail_url = forms.URLField(
        label="Image de couverture (URL)",
        max_length=500,
        required=False,
        widget=forms.HiddenInput(),
        help_text="Adresse d'une image affichée sur la fiche du cours. Facultatif.",
    )
    status = forms.ChoiceField(label="Statut", choices=STATUS_CHOICES, initial="draft")

    def __init__(self, *args, course: dict | None = None, **kwargs):
        self.course = course
        super().__init__(*args, **kwargs)
        if course and not self.is_bound:
            self.initial.update({
                "title": course.get("title", ""),
                "slug": course.get("slug", ""),
                "description": course.get("description", ""),
                "objectives": "\n".join(course.get("objectives") or []),
                "category": course.get("category", CATEGORY_CHOICES[0][0]),
                "level": course.get("level", LEVEL_CHOICES[0][0]),
                "duration_hours": course.get("duration_hours", 0),
                "language": course.get("language", "fr"),
                "is_free": course.get("is_free", False),
                "price": course.get("price") or "",
                "thumbnail": course.get("thumbnail_url") or course.get("thumbnail", ""),
                "thumbnail_url": course.get("thumbnail_url") or course.get("thumbnail", ""),
                "status": course.get("status", "draft"),
            })
        errors = self.errors if self.is_bound else {}
        for name in self.fields:
            bound_field = self[name]
            described_by = f"{bound_field.auto_id}_help"
            if name in errors:
                described_by = f"{described_by} {bound_field.auto_id}_error"
                bound_field.field.widget.attrs["aria-invalid"] = "true"
            bound_field.field.widget.attrs["aria-describedby"] = described_by

    def clean_title(self):
        title = " ".join(self.cleaned_data["title"].split())
        if len(title) < 3:
            raise forms.ValidationError("Le titre doit contenir au moins 3 caractères.")
        return title

    def clean_description(self):
        return self.cleaned_data["description"].strip()

    def clean_language(self):
        return " ".join((self.cleaned_data.get("language") or "fr").split())[:50]

    def clean_objectives(self):
        raw = self.cleaned_data.get("objectives") or ""
        return [" ".join(line.split()) for line in raw.splitlines() if line.strip()]

    def clean_slug(self):
        raw_slug = (self.cleaned_data.get("slug") or "").strip()
        if not raw_slug:
            return ""
        slug = slugify(raw_slug)[:180]
        if not slug:
            raise forms.ValidationError("Utilisez des lettres, des chiffres et des tirets.")
        exclude_id = self.course.get("_id") if self.course else None
        try:
            if repository.slug_exists(slug, exclude_id=exclude_id):
                raise forms.ValidationError("Ce slug est déjà utilisé par un autre cours.")
        except PyMongoError as error:
            raise forms.ValidationError(
                "Impossible de vérifier la disponibilité du slug pour le moment."
            ) from error
        return slug

    def clean(self):
        cleaned = super().clean()
        is_free = cleaned.get("is_free", False)
        price = cleaned.get("price")
        if is_free:
            cleaned["price"] = Decimal("0")
        elif price is None and "price" not in self.errors:
            self.add_error("price", "Indiquez un prix ou cochez « Cours gratuit ».")
        return cleaned

    def to_document(self) -> dict:
        data = dict(self.cleaned_data)
        data["price"] = float(data.get("price") or 0)
        data["duration_hours"] = float(data.get("duration_hours") or 0)
        data["language"] = data.get("language") or "fr"
        data["is_free"] = bool(data.get("is_free"))
        thumbnail = (data.get("thumbnail_url") or data.get("thumbnail") or "").strip()
        data["thumbnail"] = thumbnail
        data["thumbnail_url"] = thumbnail
        data["objectives"] = data.get("objectives") or []
        return data
