THEME_CHOICES = (
    ("programmation", "Programmation"),
    ("web", "Développement web"),
    ("data", "Bases de données"),
    ("other", "Autre"),
)

LEVEL_CHOICES = (
    ("beginner", "Débutant"),
    ("intermediate", "Intermédiaire"),
    ("advanced", "Avancé"),
)

PUBLIC_SORT_CHOICES = (
    ("recent", "Plus récents"),
    ("title_asc", "Titre (A à Z)"),
    ("title_desc", "Titre (Z à A)"),
    ("oldest", "Plus anciens"),
)

MANAGEMENT_SORT_CHOICES = (
    ("recent", "Plus récents"),
    ("oldest", "Plus anciens"),
    ("title_asc", "Titre (A à Z)"),
    ("title_desc", "Titre (Z à A)"),
)

STATUS_FILTER_CHOICES = (
    ("", "Tous les statuts"),
    ("published", "Publié"),
    ("draft", "Brouillon"),
)
