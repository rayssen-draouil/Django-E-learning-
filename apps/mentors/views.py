from django.shortcuts import render

from apps.users.decorators import roles_required


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


@roles_required("student")
def backoffice_mentors(request):
    return render(request, "backoffice/section_dashboard.html", {
        "user": request.mongo_user,
        "section": "mentors",
        "title": "Dashboard des mentors",
        "intro": "Trouvez le bon accompagnement pour atteindre vos objectifs.",
        "stats": [
            ("Mentors disponibles", "18", "light-blue"),
            ("Sessions réalisées", "32", "light-orange"),
            ("Mentor préféré", "4.9/5", "light-purple"),
            ("Heures de coaching", "46h", "light-green"),
        ],
        "items": [
            ("Amine Benali", "Python & architecture logicielle", "Disponible", "12 ans d'expérience", "user-1.jpg"),
            ("Sofia Martin", "Design produit & UX", "Disponible", "9 ans d'expérience", "user-2.jpg"),
            ("Lucas Bernard", "Data science & IA", "Demain à 14h", "10 ans d'expérience", "user-3.jpg"),
        ],
    })
