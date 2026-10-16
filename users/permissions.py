"""Authorization helpers shared by the learning-content views."""

from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import Answer, Course, CourseStatus, Mentor, Module, Question, Quiz

User = get_user_model()


def is_editor(user) -> bool:
    """Return True when the user may create learning content."""
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    return Mentor.objects.filter(user=user).exists()


def get_owner(obj):
    """Return the user who owns ``obj`` (course author), or None."""
    if isinstance(obj, Mentor):
        return obj.user
    course = None
    if isinstance(obj, Course):
        course = obj
    elif isinstance(obj, (Module, Quiz)):
        course = obj.course
    elif isinstance(obj, Question):
        course = obj.quiz.course
    elif isinstance(obj, Answer):
        course = obj.question.quiz.course
    if not course:
        return None
    if course.owner_id:
        return course.owner
    return course.mentor.user if course.mentor_id else None


def can_edit(user, obj) -> bool:
    """Return True when the user may update or delete ``obj``."""
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    owner = get_owner(obj)
    return owner is not None and owner.pk == user.pk


def visible_courses(user):
    """Courses the given user is allowed to see (published, own, or all for staff)."""
    queryset = Course.all_objects.all()
    if getattr(user, "is_staff", False):
        return queryset
    if not getattr(user, "is_authenticated", False):
        return queryset.filter(is_deleted=False, status=CourseStatus.PUBLISHED)
    return queryset.filter(
        Q(is_deleted=False, status=CourseStatus.PUBLISHED)
        | Q(owner=user)
        | Q(mentor__user=user)
    )


def owned_courses(user):
    """Courses the given user may edit (all of them for staff)."""
    queryset = Course.all_objects.all()
    if user.is_staff:
        return queryset
    return queryset.filter(Q(owner=user) | Q(mentor__user=user))


def mentors_queryset(user):
    """Mentor profiles visible in forms for the given user."""
    queryset = Mentor.objects.select_related("user")
    if not getattr(user, "is_authenticated", False):
        return Mentor.objects.none()
    if user.is_staff:
        return queryset
    return queryset.filter(user=user)
