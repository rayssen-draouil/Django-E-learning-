from django.shortcuts import render

from apps.users.decorators import roles_required
from .catalog import COURSES


def courses(request):
    return render(request, "frontoffice/learning_page.html", {
        "page_title": "Cours",
        "page_intro": "Développez vos compétences avec des parcours conçus pour progresser à votre rythme.",
        "items": [(course["title"], course["description"], course["detail"]) for course in COURSES],
        "item_label": "cours",
    })


@roles_required("instructor", "admin")
def backoffice_courses(request):
    return render(request, "backoffice/section_dashboard.html", {
        "user": request.mongo_user,
        "section": "courses",
        "title": "Dashboard des cours",
        "intro": "Gérez vos parcours et reprenez votre apprentissage.",
        "stats": [
            ("Cours disponibles", "24", "light-blue"),
            ("Cours en cours", "06", "light-orange"),
            ("Cours terminés", "12", "light-purple"),
            ("Progression moyenne", "68%", "light-green"),
        ],
        "items": [
            ("Python pour débutants", "Programmation", "75%", "12 modules", "product-1.jpg"),
            ("Développement web", "Technologies web", "48%", "18 modules", "product-2.jpg"),
            ("Bases de données", "Data & backend", "100%", "10 modules", "product-3.jpg"),
        ],
    })
