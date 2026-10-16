"""Seed the database with demo courses and their modules.

Usage: uv run python manage.py seed_demo
The command is idempotent: courses are matched on slug, modules on (course, title).
It creates only courses and modules, with categories and a demo owner user.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from users.models import Category, Course, CourseLevel, CourseStatus, Module

User = get_user_model()

DEMO_OWNER_EMAIL = "demo@skillforge.local"

COURSES = [
    {
        "title": "Python pour débutants",
        "level": CourseLevel.BEGINNER,
        "category": ("Python", "python"),
        "short_description": "Les bases de Python avec une progression claire et pratique.",
        "duration_hours": 12,
        "description": (
            "Apprenez les bases de Python : variables, conditions, boucles et "
            "fonctions, avec des exercices pratiques corrigés à chaque étape."
        ),
        "modules": [
            "Installer Python et son environnement de travail",
            "Variables, types de données et opérateurs",
            "Conditions et boucles",
            "Fonctions et modules",
            "Manipuler des listes et des dictionnaires",
        ],
    },
    {
        "title": "Développement web",
        "level": CourseLevel.INTERMEDIATE,
        "category": ("Web", "web"),
        "short_description": "Construisez des interfaces modernes, accessibles et responsives.",
        "duration_hours": 16,
        "description": (
            "Construisez des interfaces modernes, rapides et accessibles en "
            "maîtrisant les fondamentaux du HTML, du CSS et des bonnes pratiques "
            "du développement front-end."
        ),
        "modules": [
            "Structurer une page avec un HTML sémantique",
            "Mise en page responsive avec Flexbox et Grid",
            "Design system : couleurs, typographies et composants",
            "Accessibilité et performance des interfaces",
        ],
    },
    {
        "title": "Bases de données",
        "level": CourseLevel.INTERMEDIATE,
        "category": ("Data", "data"),
        "short_description": "Maîtrisez la modélisation et les requêtes SQL essentielles.",
        "duration_hours": 14,
        "description": (
            "Maîtrisez la conception et l'utilisation des bases de données "
            "relationnelles : modélisation, requêtes SQL, jointures et index."
        ),
        "modules": [
            "Modéliser des entités et des relations",
            "Créer une base avec le langage DDL",
            "Requêtes SELECT, filtres et tri",
            "Jointures, agrégats et sous-requêtes",
            "Index et optimisation des performances",
        ],
    },
    {
        "title": "Django : créer une application web",
        "level": CourseLevel.INTERMEDIATE,
        "category": ("Django", "django"),
        "short_description": "Apprenez Django en construisant une application complète.",
        "duration_hours": 18,
        "description": (
            "Développez une application web complète avec Django : modèles, "
            "vues, formulaires, templates et déploiement."
        ),
        "modules": [
            "Architecture d'un projet Django",
            "Modèles, migrations et ORM",
            "Vues, URLs et templates",
            "Formulaires et authentification",
            "Tests et mise en production",
        ],
    },
    {
        "title": "JavaScript moderne",
        "level": CourseLevel.INTERMEDIATE,
        "category": ("JavaScript", "javascript"),
        "short_description": "Découvrez le JavaScript actuel et ses usages quotidiens.",
        "duration_hours": 15,
        "description": (
            "Découvrez le JavaScript actuel : ES6+, DOM, fonctions fléchées, "
            "promesses et manipulation asynchrone des données."
        ),
        "modules": [
            "Les bases du langage et les types primitifs",
            "Fonctions, portée et contexte d'exécution",
            "Manipulation du DOM",
            "Promesses et requêtes asynchrones",
            "Modules et outillage moderne",
        ],
    },
    {
        "title": "Git et le versionnement",
        "level": CourseLevel.BEGINNER,
        "category": ("Outils", "outils"),
        "short_description": "Apprenez Git pour travailler sereinement en équipe.",
        "duration_hours": 8,
        "description": (
            "Prenez en main Git au quotidien : commits, branches, fusions et "
            "collaboration sereine sur un dépôt partagé."
        ),
        "modules": [
            "Comprendre le versionnement",
            "Commits, historique et annulation",
            "Branches et fusions",
            "Travailler en équipe avec un dépôt distant",
        ],
    },
    {
        "title": "Analyse de données avec Python",
        "level": CourseLevel.ADVANCED,
        "category": ("Data Science", "data-science"),
        "short_description": "Explorez et visualisez les données avec Python et pandas.",
        "duration_hours": 20,
        "description": (
            "Explorez, nettoyez et visualisez des données avec pandas, "
            "NumPy et matplotlib pour éclairer vos décisions."
        ),
        "modules": [
            "Introduction à NumPy et aux tableaux",
            "Nettoyage et préparation avec pandas",
            "Agrégations et jointures de données",
            "Visualisation avec matplotlib",
            "Analyser un jeu de données réel de bout en bout",
        ],
    },
    {
        "title": "Design d'interface (UI/UX)",
        "level": CourseLevel.BEGINNER,
        "category": ("Design", "design"),
        "short_description": "Concevez des interfaces lisibles, utiles et agréables.",
        "duration_hours": 10,
        "description": (
            "Apprenez à concevoir des interfaces claires et agréables : "
            "hiérarchie visuelle, grilles, composants et tests utilisateurs."
        ),
        "modules": [
            "Principes de l'ergonomie (UX)",
            "Grilles, espacements et typographie",
            "Concevoir des composants réutilisables",
            "Prototyper et tester avec de vrais utilisateurs",
        ],
    },
]

MODULE_CONTENT = (
    "Objectif : maîtriser « {title} ».\n\n"
    "1. Consulter la théorie illustrée.\n"
    "2. Reproduire l'exemple commenté.\n"
    "3. Réaliser l'exercice d'application puis vérifier la correction."
)


class Command(BaseCommand):
    help = "Seed demo courses and modules (idempotent, no mentors or quizzes)."

    @transaction.atomic
    def handle(self, *args, **options):
        owner, _ = User.objects.get_or_create(
            username=DEMO_OWNER_EMAIL,
            defaults={
                "email": DEMO_OWNER_EMAIL,
                "first_name": "Demo",
                "last_name": "Owner",
            },
        )

        course_count = 0
        module_count = 0
        for item in COURSES:
            category_name, category_slug = item["category"]
            category, _ = Category.objects.get_or_create(
                slug=category_slug,
                defaults={"name": category_name},
            )
            course, created = Course.all_objects.update_or_create(
                slug=slugify(item["title"]),
                defaults={
                    "title": item["title"],
                    "short_description": item["short_description"],
                    "description": item["description"],
                    "level": item["level"],
                    "category": category,
                    "duration_hours": item["duration_hours"],
                    "owner": owner,
                    "status": CourseStatus.PUBLISHED,
                    "is_published": True,
                    "is_deleted": False,
                },
            )
            course_count += 1 if created else 0
            for order, module_title in enumerate(item["modules"], start=1):
                _, module_created = Module.objects.update_or_create(
                    course=course,
                    title=module_title,
                    defaults={
                        "order": order,
                        "content": MODULE_CONTENT.format(title=module_title),
                    },
                )
                module_count += 1 if module_created else 0

        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready: {course_count} new course(s), "
            f"{module_count} new module(s) "
            f"({Course.objects.count()} courses, {Module.objects.count()} modules in total)."
        ))
