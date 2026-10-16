from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .factories import (
    make_answers,
    make_category,
    make_course,
    make_mentor,
    make_module,
    make_question,
    make_quiz,
    make_user,
)
from .models import Answer, Course, CourseStatus, Mentor, Module, Question, Quiz

User = get_user_model()


class CourseListTests(TestCase):
    def test_anonymous_visitors_only_see_published_courses(self):
        make_course(title="Publié", status=CourseStatus.PUBLISHED)
        make_course(title="Brouillon secret", status=CourseStatus.DRAFT)

        response = self.client.get(reverse("users:courses"))

        self.assertContains(response, "Publié")
        self.assertNotContains(response, "Brouillon secret")

    def test_owners_see_their_own_draft_courses(self):
        owner = make_user(email="owner@example.com")
        make_course(owner=owner, title="Brouillon du propriétaire", status=CourseStatus.DRAFT)
        self.client.force_login(owner)

        response = self.client.get(reverse("users:courses"))

        self.assertContains(response, "Brouillon du propriétaire")

    def test_search_filters_by_keyword(self):
        make_course(title="Python pour débutants",
                    description="Apprenez à programmer pas à pas.")
        make_course(title="Bases de données",
                    description="Modélisation et requêtes relationnelles.")

        response = self.client.get(reverse("users:courses"), {"q": "python"})

        self.assertContains(response, "Python pour débutants")
        self.assertNotContains(response, "Bases de données")

    def test_filter_by_level(self):
        make_course(title="Parcours pour experts", level="advanced")

        response = self.client.get(reverse("users:courses"), {"level": "advanced"})

        self.assertContains(response, "Parcours pour experts")

        response = self.client.get(reverse("users:courses"), {"level": "beginner"})
        self.assertNotContains(response, "Parcours pour experts")

    def test_filter_by_category(self):
        category = make_category(name="Web", slug="web")
        make_course(title="Intégration", category=category)
        other_category = make_category(name="Data", slug="data")
        make_course(title="Analyse", category=other_category)

        response = self.client.get(reverse("users:courses"), {"category": category.pk})

        self.assertContains(response, "Intégration")
        self.assertNotContains(response, "Analyse")

    def test_staff_can_filter_by_status(self):
        staff = make_user(email="staff@example.com", is_staff=True)
        make_course(title="Cours publié", status=CourseStatus.PUBLISHED)
        archived = make_course(title="Cours archivé", status=CourseStatus.ARCHIVED)
        self.client.force_login(staff)

        response = self.client.get(reverse("users:courses"), {"status": CourseStatus.ARCHIVED})

        self.assertContains(response, "Cours archivé")
        self.assertNotContains(response, "Cours publié")
        self.assertEqual(list(response.context["courses"]), [archived])

    def test_list_is_paginated(self):
        for index in range(12):
            make_course(title=f"Cours {index:02d}")

        page_one = self.client.get(reverse("users:courses"))
        page_two = self.client.get(reverse("users:courses"), {"page": 2})

        self.assertEqual(len(page_one.context["courses"]), 9)
        self.assertContains(page_one, "Cours 11")
        self.assertContains(page_two, "Cours 00")
        self.assertNotContains(page_two, "Cours 11")

    def test_course_list_avoids_n_plus_one_queries(self):
        owner = make_user(email="owner-n1@example.com")
        for index in range(4):
            course = make_course(owner=owner, title=f"Coursp {index}")
            make_module(course=course, title=f"Module {index}")

        with self.assertNumQueries(3):
            response = self.client.get(reverse("users:courses"))
            self.assertEqual(response.status_code, 200)

    def test_staff_can_publish_archive_and_export_courses(self):
        staff = make_user(email="staff-bulk@example.com", is_staff=True)
        published = make_course(title="Publié", status=CourseStatus.PUBLISHED)
        draft = make_course(title="Brouillon", status=CourseStatus.DRAFT)
        self.client.force_login(staff)

        response = self.client.post(
            reverse("users:courses"),
            {
                "bulk_action": "archive",
                "selected_courses": [published.pk, draft.pk],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cours archivé")
        published.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(published.status, CourseStatus.ARCHIVED)
        self.assertEqual(draft.status, CourseStatus.ARCHIVED)

        export = self.client.get(reverse("users:courses"), {"export": "csv"})
        self.assertEqual(export.status_code, 200)
        self.assertContains(export, "Titre,Slug,Catégorie")


class CourseDetailTests(TestCase):
    def test_published_course_detail_is_public(self):
        course = make_course(title="Cours public")
        response = self.client.get(course.get_absolute_url())
        self.assertContains(response, "Cours public")

    def test_draft_course_detail_returns_404_for_anonymous_users(self):
        course = make_course(status=CourseStatus.DRAFT)
        response = self.client.get(course.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_owner_can_view_their_draft_course(self):
        owner = make_user(email="owner-draft@example.com")
        course = make_course(owner=owner, status=CourseStatus.DRAFT)
        self.client.force_login(owner)

        response = self.client.get(course.get_absolute_url())

        self.assertContains(response, course.title)

    def test_unknown_course_returns_404(self):
        response = self.client.get(reverse("users:course_detail", args=["cours-inexistant"]))
        self.assertEqual(response.status_code, 404)

    def test_detail_page_lists_modules_and_related_courses(self):
        course = make_course()
        make_module(course=course, title="Module visible")
        make_course(category=course.category, title="Cours associé")

        response = self.client.get(course.get_absolute_url())

        self.assertContains(response, "Module visible")
        self.assertContains(response, "Cours associé")


class CourseCreateUpdateDeleteTests(TestCase):
    def test_anonymous_users_are_redirected_to_login(self):
        response = self.client.get(reverse("users:course_create"))
        self.assertRedirects(
            response,
            f"{reverse('users:login')}?next={reverse('users:course_create')}",
        )

    def test_authenticated_user_can_create_a_course_and_owns_it(self):
        user = make_user(email="creator@example.com")
        category = make_category()
        self.client.force_login(user)

        response = self.client.post(
            reverse("users:course_create"),
            {
                "title": "Nouveau cours",
                "short_description": "Résumé du cours.",
                "description": "Un cours créé depuis les tests.",
                "level": "intermediate",
                "category": str(category.pk),
                "duration_hours": 12,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        course = Course.objects.get(title="Nouveau cours")
        self.assertEqual(course.owner, user)
        self.assertEqual(course.status, CourseStatus.DRAFT)
        self.assertContains(response, "a été créé")

    def test_staff_can_create_a_course(self):
        staff = make_user(email="staff@example.com", is_staff=True)
        category = make_category(name="Staff", slug="staff-category")
        self.client.force_login(staff)

        self.client.post(
            reverse("users:course_create"),
            {
                "title": "Cours staff",
                "short_description": "Résumé staff.",
                "description": "Créé par le staff.",
                "level": "advanced",
                "category": str(category.pk),
                "duration_hours": 8,
            },
        )

        course = Course.objects.get(title="Cours staff")
        self.assertEqual(course.owner, staff)

    def test_owner_can_update_their_course(self):
        owner = make_user(email="owner-edit@example.com")
        category = make_category(name="Edition", slug="edition")
        course = make_course(owner=owner, title="Ancien titre", status=CourseStatus.DRAFT)
        self.client.force_login(owner)

        response = self.client.post(
            reverse("users:course_update", args=[course.slug]),
            {
                "title": "Nouveau titre",
                "short_description": "Résumé modifié.",
                "description": "Description mise à jour.",
                "level": "beginner",
                "category": str(category.pk),
                "duration_hours": 10,
            },
            follow=True,
        )

        course.refresh_from_db()
        self.assertEqual(course.title, "Nouveau titre")
        self.assertContains(response, "a été mis à jour")

    def test_another_user_cannot_edit_someone_elses_course(self):
        owner = make_user(email="owner@example.com")
        other = make_user(email="other@example.com")
        course = make_course(owner=owner, title="Cours protégé")
        self.client.force_login(other)

        response = self.client.post(
            reverse("users:course_update", args=[course.slug]),
            {
                "title": "Titre piraté",
                "description": "Tentative de modification.",
                "level": "beginner",
                "category": str(course.category.pk),
                "duration_hours": 12,
            },
        )

        course.refresh_from_db()
        self.assertEqual(course.title, "Cours protégé")
        self.assertEqual(response.status_code, 403)

    def test_staff_can_edit_any_course(self):
        owner = make_user(email="owner-staff@example.com")
        course = make_course(owner=owner, title="Cours suivi")
        staff = make_user(email="staff@example.com", is_staff=True)
        self.client.force_login(staff)

        self.client.post(
            reverse("users:course_update", args=[course.slug]),
            {
                "title": "Cours relu",
                "description": "Corrigé par le staff.",
                "level": "beginner",
                "category": str(course.category.pk),
                "duration_hours": 12,
            },
        )

        course.refresh_from_db()
        self.assertEqual(course.title, "Cours relu")

    def test_delete_shows_a_confirmation_page_and_requires_post(self):
        owner = make_user(email="owner-delete@example.com")
        course = make_course(owner=owner, title="À supprimer")
        self.client.force_login(owner)

        confirmation = self.client.get(reverse("users:course_delete", args=[course.slug]))
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Confirmer l'archivage")
        self.assertEqual(Course.objects.filter(pk=course.pk).count(), 1)

        response = self.client.post(reverse("users:course_delete", args=[course.slug]))
        self.assertEqual(response.status_code, 302)
        course = Course.all_objects.get(pk=course.pk)
        self.assertTrue(course.is_deleted)
        self.assertEqual(course.status, CourseStatus.ARCHIVED)

    def test_owner_can_restore_deleted_course(self):
        owner = make_user(email="owner-restore@example.com")
        course = make_course(owner=owner, title="À restaurer", status=CourseStatus.DRAFT)
        course.is_deleted = True
        course.status = CourseStatus.ARCHIVED
        course.save(update_fields=["is_deleted", "status"])
        self.client.force_login(owner)

        response = self.client.post(reverse("users:course_restore", args=[course.slug]), follow=True)

        self.assertEqual(response.status_code, 200)
        course.refresh_from_db()
        self.assertFalse(course.is_deleted)
        self.assertEqual(course.status, CourseStatus.DRAFT)

    def test_owner_can_duplicate_and_toggle_publish(self):
        owner = make_user(email="owner-actions@example.com")
        course = make_course(owner=owner, title="À dupliquer", status=CourseStatus.DRAFT)
        make_module(course=course, title="Module 1")
        self.client.force_login(owner)

        duplicate = self.client.post(reverse("users:course_duplicate", args=[course.slug]), follow=True)
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(Course.objects.filter(title__startswith="À dupliquer - copie").exists())

        toggle = self.client.post(reverse("users:course_toggle_publish", args=[course.slug]), follow=True)
        self.assertEqual(toggle.status_code, 200)
        course.refresh_from_db()
        self.assertEqual(course.status, CourseStatus.PUBLISHED)

    def test_get_on_delete_url_never_deletes(self):
        owner = make_user(email="owner-get-delete@example.com")
        course = make_course(owner=owner, title="Toujours là")
        self.client.force_login(owner)

        self.client.get(reverse("users:course_delete", args=[course.slug]))

        self.assertTrue(Course.objects.filter(pk=course.pk).exists())


class ModuleTests(TestCase):
    def setUp(self):
        self.owner = make_user(email="module-owner@example.com")
        self.course = make_course(owner=self.owner)

    def test_anonymous_module_list_shows_only_published_course_modules(self):
        make_module(course=self.course, title="Module public")
        draft_course = make_course(owner=self.owner, title="Brouillon", status=CourseStatus.DRAFT)
        make_module(course=draft_course, title="Module secret")

        response = self.client.get(reverse("users:module_list"))

        self.assertContains(response, "Module public")
        self.assertNotContains(response, "Module secret")

    def test_owner_can_create_a_module_for_their_course(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("users:module_create"),
            {
                "course": str(self.course.pk),
                "title": "Nouveau module",
                "content": "Contenu du module.",
                "order": 5,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        module = Module.objects.get(title="Nouveau module")
        self.assertEqual(module.course, self.course)
        self.assertContains(response, "a été créé")

    def test_other_user_cannot_create_a_module_for_someone_elses_course(self):
        other = make_user(email="other@example.com")
        self.client.force_login(other)

        response = self.client.post(
            reverse("users:module_create"),
            {
                "course": str(self.course.pk),
                "title": "Module indésirable",
                "content": "Contenu.",
                "order": 1,
            },
        )

        self.assertFalse(Module.objects.filter(title="Module indésirable").exists())
        self.assertEqual(response.status_code, 403)

    def test_module_detail_and_delete(self):
        module = make_module(course=self.course)
        self.client.force_login(self.owner)

        detail = self.client.get(module.get_absolute_url())
        self.assertContains(detail, module.title)

        response = self.client.post(reverse("users:module_delete", args=[module.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Module.objects.filter(pk=module.pk).exists())


class MentorTests(TestCase):
    def test_user_can_create_their_own_mentor_profile(self):
        user = make_user(email="new@example.com")
        self.client.force_login(user)

        response = self.client.post(
            reverse("users:mentor_create"),
            {"bio": "Bio du nouveau mentor.", "specialties": "Django, API"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        mentor = Mentor.objects.get(user=user)
        self.assertContains(response, mentor.display_name)

    def test_a_user_with_a_profile_cannot_create_another_one(self):
        mentor = make_mentor()
        self.client.force_login(mentor.user)

        response = self.client.get(reverse("users:mentor_create"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("users:mentors"))

    def test_mentor_can_update_their_own_profile(self):
        mentor = make_mentor(specialties="Python")
        self.client.force_login(mentor.user)

        self.client.post(
            reverse("users:mentor_update", args=[mentor.pk]),
            {
                "bio": "Bio mise à jour.",
                "specialties": "Python, Django",
            },
        )

        mentor.refresh_from_db()
        self.assertEqual(mentor.specialties, "Python, Django")

    def test_another_mentor_cannot_update_the_profile(self):
        mentor = make_mentor()
        other_user = make_user(email="intruder@example.com")
        self.client.force_login(other_user)
        make_mentor(user=other_user)

        response = self.client.post(
            reverse("users:mentor_update", args=[mentor.pk]),
            {"bio": "Bio piratée.", "specialties": "Hack"},
        )

        mentor.refresh_from_db()
        self.assertEqual(mentor.bio, "Un mentor expérimenté.")
        self.assertEqual(response.status_code, 302)


class QuizAndQuestionTests(TestCase):
    def setUp(self):
        self.mentor = make_mentor()
        self.course = make_course(mentor=self.mentor)
        self.quiz = make_quiz(course=self.course)

    def test_quiz_list_shows_published_quizzes(self):
        response = self.client.get(reverse("users:quiz"))
        self.assertContains(response, self.quiz.title)

    def test_quiz_detail_lists_questions_and_answers(self):
        question = make_question(quiz=self.quiz, text="Question secrète ?")
        make_answers(question)

        response = self.client.get(self.quiz.get_absolute_url())

        self.assertContains(response, "Question secrète ?")
        self.assertContains(response, "Réponse A")

    def test_mentor_can_create_a_quiz_for_their_course(self):
        self.client.force_login(self.mentor.user)

        response = self.client.post(
            reverse("users:quiz_create"),
            {
                "title": "Quiz de test",
                "description": "Un quiz complet.",
                "course": str(self.course.pk),
                "is_published": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Quiz.objects.filter(title="Quiz de test").exists())
        self.assertContains(response, "a été créé")

    def _question_payload(self, **overrides):
        data = {
            "text": "Quelle réponse est correcte ?",
            "score": 3,
            "order": 1,
            "answers-TOTAL_FORMS": "2",
            "answers-INITIAL_FORMS": "0",
            "answers-MIN_NUM_FORMS": "1",
            "answers-MAX_NUM_FORMS": "1000",
            "answers-0-text": "Bonne réponse",
            "answers-0-is_correct": "on",
            "answers-1-text": "Mauvaise réponse",
        }
        data.update(overrides)
        return data

    def test_mentor_can_create_a_question_with_answers(self):
        self.client.force_login(self.mentor.user)

        response = self.client.post(
            reverse("users:question_create", args=[self.quiz.pk]),
            self._question_payload(),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        question = Question.objects.get(text="Quelle réponse est correcte ?")
        self.assertEqual(question.quiz, self.quiz)
        self.assertEqual(Answer.objects.filter(question=question).count(), 2)
        self.assertContains(response, "a été ajoutée")

    def test_question_without_a_single_correct_answer_is_rejected(self):
        self.client.force_login(self.mentor.user)

        response = self.client.post(
            reverse("users:question_create", args=[self.quiz.pk]),
            self._question_payload(**{"answers-1-is_correct": "on"}),
        )

        # Two correct answers flagged -> rejected
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Cochez exactement une bonne réponse.",
            response.context["answer_formset"].non_form_errors(),
        )
        self.assertEqual(Question.objects.count(), 0)

    def test_another_mentor_cannot_add_questions(self):
        other = make_mentor(user=make_user(email="other@example.com"))
        self.client.force_login(other.user)

        response = self.client.post(
            reverse("users:question_create", args=[self.quiz.pk]),
            self._question_payload(),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Question.objects.count(), 0)

    def test_question_can_be_deleted_by_its_owner(self):
        question = make_question(quiz=self.quiz)
        self.client.force_login(self.mentor.user)

        response = self.client.post(reverse("users:question_delete", args=[question.pk]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Question.objects.filter(pk=question.pk).exists())


class NotFoundPageTests(TestCase):
    def test_unknown_url_renders_the_french_404_page(self):
        response = self.client.get("/page-inexistante/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page introuvable", status_code=404)
