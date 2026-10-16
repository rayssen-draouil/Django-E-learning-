from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .factories import make_answers, make_course, make_mentor, make_module, make_question, make_quiz, make_user
from .models import Category, Course, CourseStatus, Mentor, Module, Question, Quiz

User = get_user_model()


class MentorModelTests(TestCase):
    def test_display_name_falls_back_to_username(self):
        user = User.objects.create_user(username="ghost", email="ghost@example.com")
        mentor = Mentor.objects.create(user=user, specialties="SQL", bio="Bio.")
        self.assertEqual(mentor.display_name, "ghost")
        self.assertEqual(str(mentor), "ghost")

    def test_display_name_uses_full_name_when_available(self):
        user = make_user(first_name="Sofia", last_name="Martin")
        mentor = make_mentor(user=user)
        self.assertEqual(mentor.display_name, "Sofia Martin")

    def test_mentor_detail_url(self):
        mentor = make_mentor()
        self.assertEqual(reverse("users:mentor_detail", args=[mentor.pk]),
                         f"/users/mentors/{mentor.pk}/")


class CourseModelTests(TestCase):
    def test_module_count_uses_annotation_when_available(self):
        course = make_course()
        make_module(course=course, title="Un", order=1)
        make_module(course=course, title="Deux", order=2)
        self.assertEqual(course.module_count, 2)
        self.assertEqual(course.modules_count, 2)

        annotated = Course.objects.get(pk=course.pk)
        annotated.annotated_module_count = 42
        self.assertEqual(annotated.module_count, 42)

    def test_default_manager_excludes_soft_deleted_courses(self):
        deleted = make_course(title="Supprimé", is_deleted=True)

        self.assertTrue(Course.all_objects.filter(pk=deleted.pk).exists())
        self.assertFalse(Course.objects.filter(pk=deleted.pk).exists())

    def test_published_manager_filters_out_non_published_courses(self):
        published = make_course(title="Publié", status=CourseStatus.PUBLISHED)
        make_course(title="Brouillon", status=CourseStatus.DRAFT)

        self.assertEqual(list(Course.objects.published()), [published])

    def test_get_absolute_url_points_to_detail_page(self):
        course = make_course()
        self.assertEqual(course.get_absolute_url(), f"/users/cours/{course.slug}/")

    def test_slug_is_generated_from_the_title(self):
        course = make_course(title="Python pour débutants")
        self.assertEqual(course.slug, "python-pour-debutants")

    def test_duplicate_titles_get_distinct_slugs(self):
        first = make_course(title="Apprendre Python")
        second = make_course(title="Apprendre Python")
        self.assertNotEqual(first.slug, second.slug)
        self.assertTrue(second.slug.startswith("apprendre-python"))

    def test_explicit_slug_is_kept(self):
        course = make_course(title="Un titre", slug="mon-slug")
        self.assertEqual(course.slug, "mon-slug")

    def test_str_returns_title(self):
        self.assertEqual(str(make_course(title="SQL")), "SQL")

    def test_category_str_returns_name(self):
        category = Category.objects.create(name="Data", slug="data")
        self.assertEqual(str(category), "Data")


class ModuleModelTests(TestCase):
    def test_modules_are_ordered_by_order_then_id(self):
        course = make_course()
        second = make_module(course=course, title="B", order=2)
        first = make_module(course=course, title="A", order=1)
        self.assertEqual(list(Module.objects.filter(course=course)), [first, second])

    def test_order_must_be_unique_per_course(self):
        course = make_course()
        make_module(course=course, title="A", order=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_module(course=course, title="B", order=1)

    def test_deleting_a_course_removes_its_modules(self):
        course = make_course()
        make_module(course=course)
        course.delete()
        self.assertEqual(Module.objects.count(), 0)


class QuizModelTests(TestCase):
    def test_total_score_sums_question_scores(self):
        quiz = make_quiz()
        make_question(quiz=quiz, text="Q1", score=3)
        make_question(quiz=quiz, text="Q2", score=5)
        self.assertEqual(quiz.total_score, 8)
        self.assertEqual(quiz.question_count, 2)

    def test_correct_answer_returns_the_flagged_answer(self):
        question = make_question()
        answers = make_answers(question)
        self.assertEqual(question.correct_answer, answers[0])
        answers[0].is_correct = False
        answers[0].save()
        self.assertIsNone(question.correct_answer)

    def test_deleting_a_quiz_removes_questions_and_answers(self):
        question = make_question()
        make_answers(question)
        question.quiz.delete()
        self.assertEqual(Question.objects.count(), 0)
