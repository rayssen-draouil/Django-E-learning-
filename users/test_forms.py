import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from .factories import make_category, make_mentor, make_question, make_user
from .forms import (
    AnswerFormSet,
    CourseForm,
    MAX_IMAGE_SIZE,
    MentorForm,
    QuestionForm,
    QuestionFormSetMixin,
)
from .models import Mentor


def image_file(name="photo.png", image_format="PNG", size=(10, 10)):
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(30, 120, 200)).save(buffer, format=image_format)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class CourseFormTests(TestCase):
    def setUp(self):
        self.mentor = make_mentor()
        self.staff = make_user(email="staff@example.com", is_staff=True)
        self.category = make_category()

    def test_valid_course_data_passes(self):
        form = CourseForm(
            data={
                "title": "Nouveau cours",
                "short_description": "Une synthèse rapide.",
                "description": "Une description suffisamment longue.",
                "level": "beginner",
                "category": str(self.category.pk),
                "duration_hours": 12,
            },
            user=self.staff,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_slug_with_invalid_characters_is_rejected(self):
        form = CourseForm(
            data={
                "title": "Cours",
                "short_description": "Résumé.",
                "description": "Une description suffisamment longue.",
                "level": "beginner",
                "slug": "pas un slug!",
                "category": str(self.category.pk),
                "duration_hours": 12,
            },
            user=self.staff,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("slug", form.errors)

    def test_image_with_unknown_extension_is_rejected(self):
        bad_file = SimpleUploadedFile("evil.svg", b"<svg></svg>", content_type="image/svg+xml")
        form = CourseForm(
            data={
                "title": "Cours",
                "short_description": "Résumé.",
                "description": "Desc",
                "level": "beginner",
                "category": str(self.category.pk),
                "duration_hours": 12,
            },
            files={"image": bad_file},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)

    def test_oversized_image_is_rejected(self):
        big_file = SimpleUploadedFile("big.jpg", b"0" * (MAX_IMAGE_SIZE + 1),
                                      content_type="image/jpeg")
        form = CourseForm(
            data={
                "title": "Cours",
                "short_description": "Résumé.",
                "description": "Desc",
                "level": "beginner",
                "category": str(self.category.pk),
                "duration_hours": 12,
            },
            files={"image": big_file},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)

    def test_image_that_is_not_a_real_image_is_rejected(self):
        fake = SimpleUploadedFile("fake.jpg", b"not-an-image", content_type="image/jpeg")
        form = CourseForm(
            data={
                "title": "Cours",
                "short_description": "Résumé.",
                "description": "Desc",
                "level": "beginner",
                "category": str(self.category.pk),
                "duration_hours": 12,
            },
            files={"image": fake},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)

    def test_valid_image_is_accepted(self):
        form = CourseForm(
            data={
                "title": "Cours",
                "short_description": "Résumé.",
                "description": "Desc",
                "level": "beginner",
                "category": str(self.category.pk),
                "duration_hours": 12,
            },
            files={"image": image_file()},
        )
        self.assertTrue(form.is_valid(), form.errors)


class MentorFormTests(TestCase):
    def test_creating_a_second_profile_for_the_same_user_is_rejected(self):
        mentor_user = make_user(email="sofia@example.com")
        make_mentor(user=mentor_user)
        other_user = make_user(email="other@example.com")
        form = MentorForm(
            data={
                "user": str(mentor_user.pk),
                "bio": "Une bio valide.",
                "specialties": "UX",
            },
            user=other_user,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("user", form.errors)

    def test_staff_must_select_a_user(self):
        staff = make_user(email="staff@example.com", is_staff=True)
        form = MentorForm(data={"bio": "Une bio valide.", "specialties": "UX"}, user=staff)
        self.assertFalse(form.is_valid())
        self.assertIn("user", form.errors)

    def test_bio_is_required(self):
        user = make_user()
        form = MentorForm(data={"bio": "", "specialties": "UX"}, user=user)
        self.assertFalse(form.is_valid())
        self.assertIn("bio", form.errors)


class AnswerFormSetTests(TestCase):
    def _formset(self, answers, question=None):
        question = question or make_question()
        data = {
            "answers-TOTAL_FORMS": str(len(answers)),
            "answers-INITIAL_FORMS": "0",
            "answers-MIN_NUM_FORMS": "1",
            "answers-MAX_NUM_FORMS": "1000",
        }
        for index, (text, is_correct) in enumerate(answers):
            data[f"answers-{index}-text"] = text
            if is_correct:
                data[f"answers-{index}-is_correct"] = "on"
        return AnswerFormSet(data, instance=question, prefix="answers")

    def test_at_least_one_answer_is_required(self):
        formset = self._formset([])
        self.assertFalse(formset.is_valid())

    def test_exactly_one_correct_answer_is_required(self):
        formset = self._formset([("A", False), ("B", False)])
        self.assertFalse(formset.is_valid())
        self.assertIn("Cochez exactement une bonne réponse.", formset.non_form_errors())

    def test_valid_answers_pass(self):
        formset = self._formset([("A", True), ("B", False)])
        self.assertTrue(formset.is_valid(), formset.non_form_errors())


class QuestionFormTests(TestCase):
    def test_score_must_be_positive(self):
        form = QuestionForm(data={"text": "Une question ?", "score": 0, "order": 1})
        self.assertFalse(form.is_valid())
        self.assertIn("score", form.errors)


class QuestionFormSetMixinTests(TestCase):
    def test_mixin_is_only_a_helper_for_views(self):
        self.assertTrue(callable(getattr(QuestionFormSetMixin, "get_answer_formset")))
        self.assertTrue(callable(getattr(QuestionFormSetMixin, "form_valid")))
