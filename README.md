# SkillForge e-learning

Application d'apprentissage construite avec Django 5.2.18, PyMongo et MongoDB. Les comptes applicatifs et les sessions Django sont stockés dans MongoDB. Le projet n'utilise plus de base relationnelle.

## Architecture

```text
config/                 Réglages, URLs, points d'entrée WSGI/ASGI, connexion MongoDB
apps/users/             Comptes, formulaires, règles métier, dépôt MongoDB, sessions et permissions
apps/courses/           Gestion des cours (dépôt MongoDB, formulaires, services, vues, IA, tâches et tests)
apps/mentors/           Pages de mentors et routes du domaine
apps/quizzes/           Pages de quiz et routes du domaine
templates/              Templates globaux front-office et tableaux de bord
static/                 CSS, JavaScript et images servis par Django
docs/                   Preuves d'accessibilité et d'évaluation de l'assistant IA
Dockerfile, docker-compose.yml, .github/workflows/ci.yml
backoffice/              Sources autonomes du thème HTML/Gulp
index.html, css/         Démonstration statique Skilline conservée séparément
```

Les routes publiques existantes sont conservées, y compris les noms `users:*` utilisés par les templates. Les cours sont stockés dans MongoDB via PyMongo dans la collection `courses` ; les instructeurs gèrent leurs propres cours, les administrateurs peuvent gérer tous les cours, et seuls les cours publiés apparaissent dans le catalogue public. Les quiz sont stockés dans les collections `quizzes` et `quiz_attempts` ; les auteurs peuvent gérer leurs quiz et les étudiants répondre aux quiz publiés et consulter leurs résultats. Les mentors affichent encore du contenu d'exemple en mémoire. Il n'y a pas encore d'application `enrollments` car aucun parcours d'inscription ou de progression persistant n'existe.

Les templates globaux et les ressources du thème gardent leurs chemins actuels afin de préserver les liens existants. `backoffice/` et `index.html` sont des sources statiques autonomes, et ne sont pas les templates rendus par Django.

## Course Management

The course module stores documents in the MongoDB `courses` collection and exposes
the following routes (the existing `users:*` names are preserved):

| Route | Purpose |
| --- | --- |
| `/users/cours/` | Published catalogue with title/category search, level/category filters and pagination |
| `/users/cours/<slug>/` | Published course detail; drafts are restricted to their owner or an admin |
| `/users/backoffice/cours/manage/` | Instructor dashboard containing all owned courses |
| `/users/backoffice/cours/manage/create/` | Create a course |
| `/users/backoffice/cours/manage/<id>/edit/` | Update an owned course |
| `/users/backoffice/cours/manage/<id>/publish/` | POST-only publish/unpublish toggle |
| `/users/backoffice/cours/manage/<id>/delete/` | POST-only deletion with a confirmation page |

Course documents contain `title`, unique `slug`, `description`, `category`,
`level`, `duration_hours`, non-negative `price`, `language`, optional
`thumbnail_url`, `status`, `instructor_id`, `created_at`, and `updated_at`.
The `create_course_indexes` command creates the unique slug and status,
instructor, category, and search indexes. `seed_courses` is an idempotent demo
seed command.

| Role | Catalogue | Drafts | Create/update/delete | Publish |
| --- | --- | --- | --- | --- |
| Anonymous | Published only | No | No | No |
| Student | Published only | No | No | No |
| Instructor | Published plus own drafts | Own drafts | Own courses | Own courses |
| Admin | Published plus all drafts | All drafts | All courses | All courses |

Run the course test suite and coverage report with:

```powershell
coverage run manage.py test apps.courses
coverage report
```

## Prérequis et démarrage

1. Python compatible avec Django 5.2 et MongoDB.
2. Créer un environnement virtuel et installer les dépendances :

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Copier `.env.example` vers `.env` et définir les variables dans l'environnement du processus. Le projet ne charge pas automatiquement `.env`.
4. Démarrer MongoDB et préparer les index :

   ```powershell
   python manage.py create_user_indexes
   python manage.py create_course_indexes
   ```

5. Lancer le serveur :

   ```powershell
   python manage.py runserver
   ```

Le module de paramètres par défaut est `config.settings` et charge les paramètres de développement. En production, définir `DJANGO_SETTINGS_MODULE=config.settings.production`, fournir une clé secrète, les hôtes autorisés et une URI MongoDB sécurisée. Le proxy ou serveur doit terminer HTTPS lorsque la redirection SSL est activée.

## Docker

Le compose lance MongoDB, Redis, Gunicorn et un worker Celery :

```powershell
$env:DJANGO_SECRET_KEY = "remplacez-cette-cle-locale"
docker compose up --build
```

L'application est exposée sur `http://localhost:8000`. Le compose désactive la redirection HTTPS et les cookies `Secure` pour ce démarrage HTTP local ; en production, conserver les valeurs par défaut et placer l'application derrière un reverse proxy HTTPS. `ANTHROPIC_API_KEY` peut être fournie dans l'environnement ou dans un fichier `.env` non versionné ; sans clé, le bouton IA affiche un message d'indisponibilité sans casser le formulaire. Les statiques sont collectées dans l'image et servies par WhiteNoise.

## Variables d'environnement

| Variable | Utilisation |
| --- | --- |
| `DJANGO_SECRET_KEY` | Clé de signature Django ; obligatoire en production. |
| `DJANGO_DEBUG` | Active le mode debug local ; `true` par défaut en développement. |
| `DJANGO_ALLOWED_HOSTS` | Hôtes autorisés, séparés par des virgules. |
| `MONGODB_URI` | URI MongoDB ; défaut local `mongodb://localhost:27017/`. |
| `MONGODB_DATABASE` | Base MongoDB ; défaut `e_learning`. |
| `MONGODB_SESSIONS_COLLECTION` | Collection des sessions ; défaut `django_sessions`. |
| `USERS_ADMIN_BOOTSTRAP_TOKEN` | Jeton facultatif du service de provisionnement d'un compte avec rôle admin. |
| `ANTHROPIC_API_KEY` | Clé serveur de l'assistant IA ; laisser vide désactive proprement la génération. |
| `AI_MODEL` | Modèle Anthropic ; défaut `claude-3-5-haiku-latest`. |
| `AI_TIMEOUT` | Délai maximal de l'appel IA en secondes ; défaut `20`. |
| `CELERY_BROKER_URL` | Broker Celery ; défaut `redis://localhost:6379/0`. |
| `CELERY_RESULT_BACKEND` | Backend des résultats ; en développement, backend mémoire par défaut. |
| `CELERY_TASK_ALWAYS_EAGER` | Exécute les tâches dans le processus web ; `true` en développement, `false` avec Docker. |
| `DJANGO_SECURE_SSL_REDIRECT` | Redirection HTTPS en production ; active par défaut. |
| `DJANGO_SECURE_COOKIES` | Cookies de session/CSRF `Secure` ; actif par défaut en production, désactivé pour le compose HTTP local. |
| `DJANGO_SECURE_HSTS_SECONDS` | Durée HSTS ; défaut `31536000`. |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | Inclut les sous-domaines dans HSTS ; actif par défaut. |
| `DJANGO_SECURE_HSTS_PRELOAD` | Active l'option de préchargement HSTS ; active par défaut. |

## Données, sessions et rôles

Les documents utilisateurs restent dans la collection `users`; les mots de passe sont hachés et l'adresse e-mail est protégée par un index unique. La commande `create_user_indexes` prépare cet index et l'index TTL de la collection de sessions. MongoDB supprime les sessions expirées via TTL ; le backend vérifie aussi l'expiration au chargement. Une indisponibilité MongoDB interrompt les opérations de session au lieu de simuler une session vide.

Les rôles applicatifs sont `student`, `instructor` et `admin`. Les étudiants accèdent au catalogue et à leur tableau de bord ; les instructeurs et administrateurs disposent de la gestion des cours dans `/users/backoffice/cours/manage/` (liste), `/users/backoffice/cours/manage/create/` (création), puis `/users/backoffice/cours/manage/<id>/` (détail, modification, publication, suppression). L'administration Django native (`/admin/`) a été retirée avec les dépendances relationnelles ; aucune interface web d'administration MongoDB n'est fournie. Le provisionnement de comptes privilégiés doit être effectué par un outil d'administration contrôlé et non exposé publiquement.

La collection `courses` contient le titre, le slug unique, la description, les objectifs d'apprentissage, la catégorie, le niveau, le prix, l'état gratuit/payant, l'URL de couverture, le statut (`draft` ou `published`), l'auteur et les horodatages. La commande `create_course_indexes` crée l'index unique sur le slug ainsi que les index de recherche et de filtrage. Les images de cours sont référencées par URL ; aucun téléversement de fichier n'est géré.

L'assistant IA de rédaction est disponible dans le formulaire de cours. Il génère une description et cinq objectifs à partir du titre et du niveau, après validation côté serveur. La requête Anthropic est exécutée dans une tâche Celery ; en développement et en test, Celery fonctionne en mode *eager* avec un backend mémoire, sans Redis. Le navigateur interroge ensuite le statut de la tâche et remplit les champs avec `textContent`, sans injection HTML. La clé `ANTHROPIC_API_KEY` n'est jamais exposée au navigateur. Les métriques (durée, succès, taux d'échec) sont stockées dans `ai_logs` et affichées par `python manage.py ai_report`.

Les messages Django sont stockés dans des cookies signés pour éviter une dépendance à la base de sessions relationnelle. Les fonctions de hachage et de validation des mots de passe fournies par Django restent utilisées ; elles ne nécessitent pas les tables Django.

## Sauvegarde et restauration

Sauvegarder la base configurée, qui contient les comptes, les sessions, les cours et les quiz :

```powershell
mongodump --uri "$env:MONGODB_URI" --db "$env:MONGODB_DATABASE" --out .\backups
```

Restaurer avec `mongorestore` vers une instance contrôlée et vérifier les collections `users`, `django_sessions`, `courses`, `quizzes` et `quiz_attempts`. Les sessions peuvent être invalidées après restauration si les clés de signature ou les paramètres de sécurité ont changé. Protéger les sauvegardes comme des données sensibles et les stocker hors du dépôt.

## Tests et vérifications

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
.\.venv\Scripts\python.exe -m coverage run manage.py test
.\.venv\Scripts\python.exe -m coverage report -m
.\.venv\Scripts\python.exe manage.py ai_report
```

La configuration `.coveragerc` mesure le module `apps/courses` et exclut ses tests. La suite couvre les formulaires, le dépôt MongoDB, les services, les permissions, les routes, l'IA (succès, délai, clé absente, réponse invalide, stockage) et les commandes de gestion. La CI GitHub Actions démarre MongoDB, exécute les tests, vérifie un seuil de couverture de 90 %, lance `check --deploy` avec les paramètres de production et construit l'image Docker.

Pour les contrôles de déploiement, configurer les variables de production puis exécuter `python manage.py check --deploy`. Les preuves d'accessibilité et d'évaluation IA sont documentées dans `docs/accessibilite.md` et `docs/ia-evaluation.md`.

## Limites connues

- Les mentors et leurs tableaux de bord restent des exemples en mémoire. Les cours, quiz et tentatives sont persistés dans MongoDB ; les statistiques de quiz sont calculées à partir des tentatives enregistrées.
- Les comptes métier MongoDB ne sont pas reliés à `django.contrib.auth`; l'authentification Django native et son panneau d'administration ne sont pas disponibles.
- L'application `enrollments`, la progression persistante, ainsi qu'une interface MongoDB d'administration restent à concevoir avec leurs règles d'accès avant leur implémentation.
- `db.sqlite3` est conservé dans l'arborescence locale pour préserver les données préexistantes, mais n'est plus configuré ni utilisé par l'application. `.gitignore` ignore les fichiers SQLite locaux. Il n'a pas été supprimé.
- Les cours ne gèrent ni téléversement d'image ni inscription/progression : la couverture est une URL et l'application `enrollments` reste à concevoir.
- L'assistant IA dépend d'un service externe et d'une clé valide. Les tests simulent l'API ; sans clé, la génération est indisponible mais le reste du CRUD fonctionne.

## Crédits des thèmes

La landing page utilise le thème Skilline de [ThemeWagon](https://themewagon.com/themes/skilline/), conçu par [Irvan Moses et mhaecal](https://github.com/mhaecal/) sous licence MIT. Les sources et attributions du thème de back-office sont conservées dans `backoffice/`.
