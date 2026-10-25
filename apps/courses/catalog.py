COURSES = (
    {
        "id": "python-debutant",
        "title": "Python pour débutants",
        "description": "Apprenez les bases de Python avec des exercices pratiques.",
        "detail": "12 modules",
        "theme": "programmation",
        "level": "beginner",
        "competencies": [
            "Écrire des instructions en Python",
            "Utiliser les variables et les types de données",
            "Résoudre des exercices avec Python",
        ],
    },
    {
        "id": "developpement-web",
        "title": "Développement web",
        "description": "Construisez des interfaces modernes et accessibles.",
        "detail": "18 modules",
        "theme": "web",
        "level": "beginner",
        "competencies": [
            "Créer des interfaces web",
            "Appliquer les principes d’accessibilité",
            "Structurer une page web",
        ],
    },
    {
        "id": "bases-de-donnees",
        "title": "Bases de données",
        "description": "Maîtrisez la conception et l'utilisation des bases de données.",
        "detail": "10 modules",
        "theme": "data",
        "level": "beginner",
        "competencies": [
            "Concevoir une base de données",
            "Organiser des données",
            "Utiliser une base de données",
        ],
    },
)

COURSES_BY_ID = {course["id"]: course for course in COURSES}
