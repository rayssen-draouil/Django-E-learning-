from django import forms

from .options import LEVEL_CHOICES


class LearningPathProposalForm(forms.Form):
    objective = forms.CharField(
        label="Objectif d’apprentissage",
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "placeholder": "Ex. apprendre à créer des applications web avec Django",
        }),
    )
    target_job = forms.CharField(
        label="Métier visé",
        max_length=160,
        required=False,
        widget=forms.TextInput(attrs={
            "placeholder": "Ex. développeur web junior",
        }),
    )
    level = forms.ChoiceField(label="Niveau", choices=LEVEL_CHOICES)
