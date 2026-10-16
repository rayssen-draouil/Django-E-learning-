"""Small helpers shared by the test modules (not a test module itself)."""

from django.contrib.auth import get_user_model

from itertools import count

from .models import Answer, Category, Course, CourseStatus, Mentor, Module, Question, Quiz


OWNER_SEQUENCE = count(1)

User = get_user_model()

PASSWORD = "A-valid-password-123!"


def make_user(email="user@example.com", first_name="Ada", last_name="Lovelace",
              **kwargs):
    return User.objects.create_user(
        username=email,
        email=email,
        password=PASSWORD,
        first_name=first_name,
        last_name=last_name,
        **kwargs,
    )


def make_mentor(user=None, specialties="Python", bio="Un mentor expérimenté."):
    if user is None:
        user = make_user(email="mentor@example.com", first_name="Amine",
                         last_name="Benali")
    return Mentor.objects.create(user=user, specialties=specialties, bio=bio)


def make_category(name="Programmation", slug="programmation", **kwargs):
    defaults = {"description": "Catégorie par défaut."}
    defaults.update(kwargs)
    category, _ = Category.objects.get_or_create(slug=slug, defaults={"name": name, **defaults})
    return category


def make_course(mentor=None, owner=None, category=None, title="Python pour débutants", **kwargs):
    if "status" not in kwargs and "is_published" in kwargs:
        kwargs.setdefault(
            "status",
            CourseStatus.PUBLISHED if kwargs["is_published"] else CourseStatus.DRAFT,
        )
    defaults = {
        "short_description": "Un parcours pour progresser pas à pas.",
        "description": "Apprenez les bases de Python.",
        "level": "beginner",
        "category": category or make_category(),
        "duration_hours": 12,
        "status": "published",
        "is_published": True,
    }
    defaults.update(kwargs)
    if mentor is not None:
        defaults.setdefault("mentor", mentor)
    if owner is None:
        owner = mentor.user if mentor is not None else make_user(email=f"owner-{next(OWNER_SEQUENCE)}@example.com")
    defaults.setdefault("owner", owner)
    return Course.objects.create(title=title, **defaults)


def make_module(course=None, title="Variables et types", order=1, **kwargs):
    if course is None:
        course = make_course()
    defaults = {"content": "Contenu du module.", "order": order}
    defaults.update(kwargs)
    return Module.objects.create(course=course, title=title, **defaults)


def make_quiz(course=None, title="Quiz fondamentaux", **kwargs):
    if course is None:
        course = make_course()
    defaults = {"description": "Vérifiez vos connaissances.", "is_published": True}
    defaults.update(kwargs)
    return Quiz.objects.create(course=course, title=title, **defaults)


def make_question(quiz=None, text="Quelle est la bonne réponse ?", order=1,
                  score=2, **kwargs):
    if quiz is None:
        quiz = make_quiz()
    defaults = {"order": order, "score": score}
    defaults.update(kwargs)
    return Question.objects.create(quiz=quiz, text=text, **defaults)


def make_answers(question):
    first = Answer.objects.create(question=question, text="Réponse A", is_correct=True)
    second = Answer.objects.create(question=question, text="Réponse B")
    return [first, second]
