from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    Category = apps.get_model("users", "Category")
    Course = apps.get_model("users", "Course")
    user_app_label, user_model_name = settings.AUTH_USER_MODEL.split(".", 1)
    User = apps.get_model(user_app_label, user_model_name)

    category, _ = Category.objects.get_or_create(
        slug="general",
        defaults={
            "name": "Général",
            "description": "Catégorie par défaut des cours existants.",
        },
    )
    fallback_owner = (
        User.objects.filter(is_staff=True).order_by("pk").first()
        or User.objects.order_by("pk").first()
    )

    for course in Course.objects.select_related("mentor__user").all():
        updates = {}
        if getattr(course, "is_published", False):
            updates["status"] = "published"
        if not course.category_id:
            updates["category_id"] = category.pk
        if not course.owner_id:
            owner = getattr(getattr(course, "mentor", None), "user", None) or fallback_owner
            if owner is not None:
                updates["owner_id"] = owner.pk
        if updates:
            Course.objects.filter(pk=course.pk).update(**updates)


def backwards(apps, schema_editor):
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_category_course_duration_hours_course_is_deleted_and_more"),
    ]

    operations = [migrations.RunPython(forwards, backwards)]