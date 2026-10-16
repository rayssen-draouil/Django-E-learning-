from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils.text import slugify


class TimeStampedModel(models.Model):
    """Base class adding creation and update timestamps."""

    created_at = models.DateTimeField("date de création", auto_now_add=True)
    updated_at = models.DateTimeField("date de modification", auto_now=True)

    class Meta:
        abstract = True


class CourseLevel(models.TextChoices):
    BEGINNER = "beginner", "Débutant"
    INTERMEDIATE = "intermediate", "Intermédiaire"
    ADVANCED = "advanced", "Avancé"


class CourseStatus(models.TextChoices):
    DRAFT = "draft", "Brouillon"
    PUBLISHED = "published", "Publié"
    ARCHIVED = "archived", "Archivé"


class Category(TimeStampedModel):
    """Course category used for grouping and filtering."""

    name = models.CharField("nom", max_length=120, unique=True)
    slug = models.SlugField("slug", max_length=140, unique=True)
    description = models.CharField("description", max_length=255, blank=True)

    class Meta:
        verbose_name = "catégorie"
        verbose_name_plural = "catégories"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("users:courses")


class CourseQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_deleted=False)

    def published(self):
        return self.active().filter(status=CourseStatus.PUBLISHED)


class CourseManager(models.Manager.from_queryset(CourseQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)

    def published(self):
        return self.get_queryset().published()


class Mentor(TimeStampedModel):
    """Teaching profile attached to a single user account."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mentor_profile",
        verbose_name="utilisateur",
    )
    bio = models.TextField("biographie", blank=True)
    specialties = models.CharField(
        "spécialités",
        max_length=200,
        help_text="Spécialités séparées par des virgules, par exemple : Python, Architecture",
    )
    photo = models.ImageField("photo", upload_to="mentors/%Y/%m/", blank=True)

    class Meta:
        verbose_name = "mentor"
        verbose_name_plural = "mentors"
        ordering = ["user__first_name", "user__last_name"]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        full_name = self.user.get_full_name()
        return full_name or self.user.get_username()

    def get_absolute_url(self) -> str:
        return reverse("users:mentor_detail", args=[self.pk])


class Course(TimeStampedModel):
    """A course published on the platform."""

    objects = CourseManager()
    all_objects = models.Manager()

    title = models.CharField("titre", max_length=200)
    slug = models.SlugField(
        "slug",
        max_length=220,
        unique=True,
        blank=True,
        help_text="Identifiant URL, généré automatiquement à partir du titre.",
    )
    short_description = models.CharField("courte description", max_length=280, blank=True)
    description = models.TextField("description")
    level = models.CharField(
        "niveau",
        max_length=20,
        choices=CourseLevel.choices,
        default=CourseLevel.BEGINNER,
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="courses",
        verbose_name="catégorie",
    )
    duration_hours = models.PositiveIntegerField(
        "durée (heures)",
        default=1,
        validators=[MinValueValidator(1)],
    )
    status = models.CharField(
        "statut",
        max_length=20,
        choices=CourseStatus.choices,
        default=CourseStatus.DRAFT,
    )
    image = models.ImageField(
        "image",
        upload_to="courses/%Y/%m/",
        blank=True,
        help_text="Formats acceptés : JPEG, PNG ou WEBP (2 Mo maximum).",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_courses",
        verbose_name="propriétaire",
        null=True,
        blank=True,
    )
    mentor = models.ForeignKey(
        Mentor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="courses",
        verbose_name="mentor",
    )
    is_published = models.BooleanField("publié", default=False)
    is_deleted = models.BooleanField("supprimé", default=False)

    class Meta:
        verbose_name = "cours"
        verbose_name_plural = "cours"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["category"], name="course_category_idx"),
            models.Index(fields=["level"], name="course_level_idx"),
            models.Index(fields=["status"], name="course_status_idx"),
            models.Index(fields=["is_published"], name="course_published_idx"),
            models.Index(fields=["is_deleted"], name="course_deleted_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        """Keep the slug unique and in sync with the title when left empty."""
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.title)[:200] or "cours"
        slug = base
        counter = 2
        while Course.all_objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    @property
    def modules_count(self) -> int:
        """Number of modules, reusing an annotated value when available."""
        annotated = getattr(self, "annotated_module_count", None)
        if annotated is not None:
            return annotated
        return self.modules.count()

    @property
    def module_count(self) -> int:
        return self.modules_count

    def get_absolute_url(self) -> str:
        return reverse("users:course_detail", kwargs={"slug": self.slug})


class Module(TimeStampedModel):
    """A lesson belonging to a course."""

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="modules",
        verbose_name="cours",
    )
    title = models.CharField("titre", max_length=200)
    content = models.TextField("contenu")
    order = models.PositiveIntegerField(
        "ordre",
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Position du module dans le cours (1 par défaut).",
    )

    class Meta:
        verbose_name = "module"
        verbose_name_plural = "modules"
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "order"],
                name="unique_module_order_per_course",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.course.title} · {self.title}"

    def get_absolute_url(self) -> str:
        return reverse("users:module_detail", args=[self.pk])


class Quiz(TimeStampedModel):
    """A quiz linked to a course."""

    title = models.CharField("titre", max_length=200)
    description = models.TextField("description", blank=True)
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="quizzes",
        verbose_name="cours",
    )
    is_published = models.BooleanField("publié", default=False)

    class Meta:
        verbose_name = "quiz"
        verbose_name_plural = "quiz"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.title

    @property
    def question_count(self) -> int:
        annotated = getattr(self, "annotated_question_count", None)
        if annotated is not None:
            return annotated
        return self.questions.count()

    @property
    def total_score(self) -> int:
        return sum(question.score for question in self.questions.all())

    def get_absolute_url(self) -> str:
        return reverse("users:quiz_detail", args=[self.pk])


class Question(models.Model):
    """A single question inside a quiz."""

    quiz = models.ForeignKey(
        Quiz,
        on_delete=models.CASCADE,
        related_name="questions",
        verbose_name="quiz",
    )
    text = models.TextField("énoncé")
    score = models.PositiveIntegerField(
        "score",
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Points obtenus pour une réponse correcte.",
    )
    order = models.PositiveIntegerField(
        "ordre",
        default=1,
        validators=[MinValueValidator(1)],
    )

    class Meta:
        verbose_name = "question"
        verbose_name_plural = "questions"
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return self.text[:60]

    @property
    def correct_answer(self) -> "Answer | None":
        return next((answer for answer in self.answers.all() if answer.is_correct), None)


class Answer(models.Model):
    """A proposed answer; exactly one per question must be correct."""

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="answers",
        verbose_name="question",
    )
    text = models.CharField("réponse", max_length=500)
    is_correct = models.BooleanField("bonne réponse", default=False)

    class Meta:
        verbose_name = "réponse"
        verbose_name_plural = "réponses"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.text
