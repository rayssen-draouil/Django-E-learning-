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

The course module stores documents in the MongoDB `courses` collection. The
canonical routes live in the `courses:` namespace (`config/urls.py`) and the
same patterns are mounted under `/users/` with the existing `users:*` names so
current templates and links keep working.

| Route | Purpose |
| --- | --- |
| `/cours/` | Published catalogue with title/category search, category/level filters and pagination |
| `/cours/<slug>/` | Published course detail; drafts are restricted to their owner or an admin |
| `/cours/<slug>/traduire/` | POST-only JSON translation of title, description and objectives (CSRF, rate limited, cached) |
| `/backoffice/cours/` | Student course page |
| `/backoffice/cours/manage/` | Instructor/admin course list with search, status filter, stats and pagination |
| `/backoffice/cours/create/` | Create a course (GET form, POST save, optional image upload) |
| `/backoffice/cours/<id>/` | Course detail with management actions |
| `/backoffice/cours/<id>/edit/` | Update an owned course (GET form, POST save) |
| `/backoffice/cours/<id>/publish/` | POST-only publish/unpublish toggle |
| `/backoffice/cours/<id>/delete/` | GET confirmation page; deletion only on POST |
| `/backoffice/cours/manage/ai/generate/` | POST-only AI course content generation |
| `/backoffice/cours/manage/ai/objectives/` | POST-only AI learning objectives |
| `/backoffice/cours/manage/ai/improve-description/` | POST-only description improvement |
| `/backoffice/cours/manage/ai/suggest-meta/` | POST-only category, level and tags suggestion |
| `/backoffice/cours/manage/ai/status/<task_id>/` | POST-only AI generation task polling |
| `/backoffice/cours/manage/ai/report/` | GET AI evaluation report (instructor and admin) |
| `/backoffice/cours/manage/ai/report/run/` | POST admin-only evaluation launch |
| `/backoffice/cours/manage/ai/report/status/<task_id>/` | GET admin-only evaluation progress |

Historical routes such as `/backoffice/cours/nouveau/`,
`/backoffice/cours/mes-cours/`, `/backoffice/cours/manage/create/` and
`/backoffice/cours/manage/<id>/...` redirect to the canonical routes. The
existing AI generation routes under `/backoffice/cours/manage/ai/` are unchanged.

Course documents contain `title`, unique `slug`, `description`, `objectives`,
`category`, `level`, `duration_hours`, non-negative `price`, `is_free`,
`language`, `image`, `image_alt`, `status`, `tags`, `instructor_id`,
`instructor_name`, `created_at`, and `updated_at`. The legacy `thumbnail` and
`thumbnail_url` fields are kept as aliases.

Course images are uploaded as `multipart/form-data`, validated by Pillow
(JPEG, PNG or WebP, 2 MB maximum by default), stored with a UUID filename under
`MEDIA_ROOT/courses/`, and only the relative path is saved in MongoDB. Updating
a course deletes the replaced file and deleting a course removes its image.
Courses without an image use the static placeholder
`static/images/course-placeholder.svg`. Django serves `/media/` in development;
in production the web server or object storage must serve `MEDIA_ROOT`.

The `create_course_indexes` command creates the unique slug and status,
instructor, category, and search indexes. `seed_courses` is an idempotent demo
seed command that also generates deterministic placeholder images for four
demo courses. Every course-management command is listed explicitly in
[Commandes de gestion des cours](#commandes-de-gestion-des-cours) below.

| Role | Catalogue | Drafts | Create/update/delete | Publish |
| --- | --- | --- | --- | --- |
| Anonymous | Published only | No | No | No |
| Student | Published only | No | No | No |
| Instructor | Published plus own drafts | Own drafts | Own courses | Own courses |
| Admin | Published plus all drafts | All drafts | All courses | All courses |

### Commandes de gestion des cours

Toutes les commandes se lancent depuis la racine du dépôt, avec MongoDB démarré
et l'environnement virtuel activé :

```powershell
.\.venv\Scripts\Activate.ps1
```

Si l'environnement n'est pas activé, préfixer chaque commande par
`.\.venv\Scripts\python.exe` (ou `.\.venv\Scripts\celery.exe` pour le worker).

| Commande | Rôle |
| --- | --- |
| `python manage.py create_user_indexes` | Prérequis comptes/sessions (hors module cours) : index unique e-mail et index TTL des sessions, nécessaires pour se connecter au back-office. |
| `python manage.py create_course_indexes` | Crée les index MongoDB des cours et des traductions. Idempotent. |
| `python manage.py seed_courses` | Insère quatre cours de démonstration de façon idempotente, génère leurs images PNG, répare les chemins d'image manquants et crée les index. Sans argument. |
| `python manage.py ai_report` | Affiche les métriques des appels IA stockées dans `ai_logs` (volume, moyenne, p95, échecs, ventilation par action). N'écrit aucun rapport. |
| `python manage.py ai_report --mock` | Écrit `report.md` et `report.json` avec des prédictions simulées parfaites (mode `mock`, aucun appel réseau, aucune clé). |
| `python manage.py ai_report --eval` | Évalue réellement les 36 cours du jeu de référence via Groq et écrit les deux rapports (mode `real`). `GROQ_API_KEY` obligatoire. |
| `python manage.py ai_report --eval --delay 1.0` | Variante avec délai personnalisé entre deux appels (défaut `2.5` secondes, valeur positive ou nulle). |
| `python manage.py runserver` | Démarre le serveur de développement et l'interface de gestion des cours. |
| `python manage.py check` | Vérifie la configuration Django locale ; `check --deploy` lance en plus les contrôles de production. |
| `python manage.py test apps.courses` | Exécute uniquement les tests du module cours. |
| `python manage.py test` | Exécute la suite complète du projet (recommandé avant une livraison). |
| `python -m coverage run manage.py test apps.courses` | Exécute les tests du module cours avec mesure de couverture. |
| `python -m coverage report -m` | Affiche la couverture et les lignes non couvertes. |
| `celery -A config worker -l info` | Optionnel : worker des tâches IA et d'évaluation lorsque `CELERY_TASK_ALWAYS_EAGER=false`. Inutile en développement eager. |

#### Index MongoDB des cours

```powershell
.\.venv\Scripts\python.exe manage.py create_course_indexes
```

La commande crée `courses_slug_unique` (slug unique), `courses_instructor_updated`,
`courses_instructor`, `courses_status_updated`, `courses_status`,
`courses_category`, `courses_text` (recherche plein texte sur le titre, la
description et les tags, langue française) ainsi que `course_translations_unique`
sur `(course_id, lang, course_updated_at)`. Elle peut être relancée sans risque.

#### Cours de démonstration

```powershell
.\.venv\Scripts\python.exe manage.py seed_courses
```

La commande est idempotente grâce au slug : elle insère « Python pour débutants »,
« Concevoir une API web », « Django pour développeurs » et « Design UX et
accessibilité » s'ils sont absents, puis répare les images manquantes des cours
déjà seedés. Elle crée aussi les index et des images placeholder déterministes
dans `MEDIA_ROOT/courses/`. Les cours utilisent l'identifiant d'instructeur de
démonstration `000000000000000000000001`, à ne pas confondre avec un compte réel
en production.

#### Métriques et évaluation IA

```powershell
# Métriques seulement (lecture de ai_logs)
.\.venv\Scripts\python.exe manage.py ai_report

# Rapport simulé, hors réseau (tests)
.\.venv\Scripts\python.exe manage.py ai_report --mock

# Évaluation réelle sur les 36 cours (clé Groq requise)
.\.venv\Scripts\python.exe manage.py ai_report --eval

# Évaluation réelle avec un délai plus court entre les appels
.\.venv\Scripts\python.exe manage.py ai_report --eval --delay 1.0
```

`--mock` et `--eval` s'excluent en pratique : si les deux sont fournis, `--mock`
est prioritaire. `--eval` nécessite `GROQ_API_KEY` dans l'environnement et
n'utilise jamais de repli simulé. Les deux modes écrivent
`apps/courses/ai_eval/report.md` et `apps/courses/ai_eval/report.json`. Le bouton
« Relancer l'évaluation » de la page Rapport IA exécute la même logique que
`--eval` en arrière-plan, avec un verrou et un délai de 10 minutes entre deux
lancements ; il est réservé aux administrateurs.

#### Tests et couverture du module cours

```powershell
# Tests ciblés du module
.\.venv\Scripts\python.exe manage.py test apps.courses

# Suite complète
.\.venv\Scripts\python.exe manage.py test

# Couverture du module (configuration .coveragerc)
.\.venv\Scripts\python.exe -m coverage run manage.py test apps.courses
.\.venv\Scripts\python.exe -m coverage report -m
```

La configuration `.coveragerc` mesure le module `apps/courses` et exclut ses
tests ; le seuil de référence du projet est de 90 %. Le dernier rapport détaillé
du module est conservé dans `apps/courses/COVERAGE.txt`.

#### Tâches asynchrones des cours

En développement, `CELERY_TASK_ALWAYS_EAGER=true` exécute les tâches IA et
l'évaluation dans le processus web ; si le broker est indisponible, un thread de
secours prend le relais et persiste son état dans MongoDB. Avec
`CELERY_TASK_ALWAYS_EAGER=false`, démarrer un worker :

```powershell
.\.venv\Scripts\celery.exe -A config worker -l info
```

Le worker utilise le broker défini par `CELERY_BROKER_URL` (Redis local par
défaut). Aucune clé ni aucun texte de cours n'est journalisé par ces commandes.

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
| `DJANGO_MEDIA_ROOT` | Répertoire des images de cours ; défaut `media/` à la racine du projet. |
| `COURSE_IMAGE_MAX_BYTES` | Taille maximale d'une image de cours ; défaut `2097152` (2 Mo). |
| `USERS_ADMIN_BOOTSTRAP_TOKEN` | Jeton facultatif du service de provisionnement d'un compte avec rôle admin. |
| `GROQ_API_KEY` | Clé serveur Groq de l'assistant IA et de la traduction des cours ; laisser vide désactive proprement ces fonctions. |
| `GROQ_MODEL` | Modèle Groq (OpenAI-compatible) ; si vide, repli `openai/gpt-oss-20b`. |
| `AI_TIMEOUT` | Délai maximal d'un appel IA en secondes ; défaut `20`. |
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

Les rôles applicatifs sont `student`, `instructor` et `admin`. Les étudiants accèdent au catalogue et à leur tableau de bord ; les instructeurs et administrateurs disposent de la gestion des cours dans `/backoffice/cours/manage/` (liste), `/backoffice/cours/create/` (création), puis `/backoffice/cours/<id>/` (détail, modification, publication, suppression). Les mêmes routes restent disponibles sous `/users/backoffice/cours/...`. L'administration Django native (`/admin/`) a été retirée avec les dépendances relationnelles ; aucune interface web d'administration MongoDB n'est fournie. Le provisionnement de comptes privilégiés doit être effectué par un outil d'administration contrôlé et non exposé publiquement.

La collection `courses` contient le titre, le slug unique, la description, les objectifs d'apprentissage, la catégorie, le niveau, le prix, l'état gratuit/payant, le chemin relatif de l'image (`image`), son texte alternatif (`image_alt`), le statut (`draft` ou `published`), l'auteur et les horodatages. La commande `create_course_indexes` crée l'index unique sur le slug ainsi que les index de recherche et de filtrage. Les images sont téléversées dans `MEDIA_ROOT/courses/` ; MongoDB ne conserve que le chemin relatif, ce qui permet de changer de stockage sans migrer les documents.

## Assistant IA (module cours)

Trois aides, jamais appliquées sans confirmation de l'instructeur :

1. **Générer avec l'IA** — 5 objectifs d'apprentissage à partir du titre et de la description.
2. **Améliorer** — suggestion de description plus courte et plus claire (Accepter / Rejeter).
3. **Suggérer catégorie et tags** — catégorie, niveau et tags validés contre les enums du formulaire.

Les appels partent en `POST` (CSRF, instructeur/admin uniquement) vers Groq (`https://api.groq.com/openai/v1/chat/completions`). Variables : `GROQ_API_KEY` et `GROQ_MODEL` lues **uniquement** dans l'environnement. Sans clé, un message français s'affiche et le formulaire reste utilisable. Timeout 20 s, un retry sur HTTP 429/5xx. Une erreur HTTP 400 est retentée une fois sans `response_format` ; une erreur HTTP 404 indique en français de vérifier `GROQ_MODEL` et le modèle par défaut `openai/gpt-oss-20b`. Les textes utilisateur sont placés dans des blocs délimités ; le modèle doit les traiter comme des données. Sortie IA non fiable : validation JSON, enums, tags ≤ 8 × 30 caractères, balises HTML retirées, rendu via `textContent` (jamais `mark_safe`).

**Mode sync/async :** `dispatch_task` tente Celery (déjà dans le projet ; en développement souvent *eager* sans Redis). Si le broker est indisponible, un thread exécute la tâche et stocke son état pendant une heure dans la collection MongoDB `course_ai_tasks`. Le navigateur interroge `ai/status/<task_id>/` en POST ; cet état reste accessible après un rechargement automatique ou depuis un autre processus web. Le JSON de génération inclut `"mode": "celery"` ou `"thread"`.

**Métriques :** chaque appel enregistre `duration_ms` dans `ai_logs`. Commandes :

```powershell
.\.venv\Scripts\python.exe manage.py ai_report
.\.venv\Scripts\python.exe manage.py ai_report --mock
.\.venv\Scripts\python.exe manage.py ai_report --eval
```

`--mock` (tests, hors réseau) calcule accuracy, précision/rappel/F1 par classe, macro-F1, matrices de confusion, taux JSON invalide, latence moyenne et p95, et écrit `apps/courses/ai_eval/report.md` ainsi que `apps/courses/ai_eval/report.json`. Le rapport indique explicitement que le mode est simulé et qu'aucune clé n'a été utilisée. `--eval` appelle vraiment Groq (`GROQ_API_KEY` obligatoire) sur le jeu `apps/courses/ai_eval/dataset.json` (≥ 30 cours étiquetés) et écrit les deux mêmes fichiers.

La page **Rapport IA** (`/backoffice/cours/manage/ai/report/`, ou `/users/backoffice/cours/manage/ai/report/`) affiche le contenu de `report.json` aux instructeurs et administrateurs, avec un lien « Rapport IA » depuis la liste des cours. Elle présente les cartes de synthèse, les métriques par classe, les matrices de confusion et l'analyse ; le mode `--mock` y apparaît sous forme de bannière « Évaluation simulée, non représentative ». Tant qu'aucun rapport n'a été généré, la page affiche un état vide avec la commande à exécuter. Les valeurs sont échappées automatiquement et les tableaux possèdent des légendes et des en-têtes de lignes/colonnes.

**Relance depuis le navigateur :** un administrateur dispose du bouton « Relancer l'évaluation ». Une confirmation rappelle les 36 cours, les 2,5 s entre appels, la durée de plusieurs minutes, la consommation de quota Groq et la limite d'un lancement toutes les 10 minutes. Le `POST` CSRF vers `/backoffice/cours/manage/ai/report/run/` (ou `/users/backoffice/cours/manage/ai/report/run/`) démarre la même évaluation que `ai_report --eval` en arrière-plan via le mécanisme de tâches existant, avec un état persisté dans `course_ai_tasks`. Le navigateur interroge en `GET` `/backoffice/cours/manage/ai/report/status/<task_id>/` et affiche « Évaluation en cours : 12/36 » dans une barre de progression `aria-live` ; la page se recharge automatiquement à la fin. Une évaluation déjà en cours est refusée avec un 409, un second lancement avant 10 minutes avec un 429 indiquant le temps restant, et l'absence de `GROQ_API_KEY` avec un 503 en français, sans repli simulé. Les deux routes sont réservées aux administrateurs, et les instructeurs ne voient ni le bouton ni le suivi.

Les rapports `report.md` et `report.json` sont écrits dans un fichier temporaire du même dossier puis remplacés par `os.replace`, uniquement à la fin d'une évaluation réussie. Un échec (clé absente, jeu illisible, panne, ou run sans aucun cas répondu) conserve les rapports précédents. Le mode `"real"`, le modèle et `generated_at` sont enregistrés dans le JSON. Ni la clé ni les textes des cours ne sont journalisés.

**Limites :** dépendance réseau Groq ; confusion possible `web`/`programming` et `data`/`programming` ; les tags ne sont pas scorés ; 10 appels/minute/utilisateur.

## Traduction des cours

La fiche publique d'un cours propose un sélecteur de langue et un bouton **Traduire** qui traduisent le titre, la description et les objectifs. Le formulaire envoie un `POST` CSRF à `/cours/<slug>/traduire/` (ou `/users/cours/<slug>/traduire/`) et reçoit un JSON `{title, description, objectives}`. Seuls les cours visibles par le visiteur sont traduisibles : un brouillon reste en 404 pour les autres personnes. Le limiteur de démonstration s'applique comme pour l'IA (10 requêtes/minute, clé par utilisateur connecté ou par session/IP pour un visiteur anonyme).

Le serveur réutilise le client Groq de l'assistant IA (`call_groq_api`, endpoint `https://api.groq.com/openai/v1/chat/completions`), sans dépendance supplémentaire. `GROQ_API_KEY` et `GROQ_MODEL` sont lues dans l'environnement à chaque appel ; la clé part dans l'en-tête `Authorization` et n'apparaît jamais dans l'URL, les messages d'erreur ou les journaux. Les textes du cours ne sont jamais journalisés : seuls l'action `translate`, la durée, le succès et la longueur de sortie sont enregistrés dans `ai_logs`.

Les textes sont placés dans des blocs délimités et présentés au modèle comme des **données** à traduire, jamais comme des instructions. Le prompt impose la langue cible parmi `fr`, `en`, `ar`, `es`, `de`, le nombre exact d'objectifs, la conservation du sens, des noms propres et des retours à la ligne. La sortie est validée strictement : objet JSON, champs texte, nombre d'objectifs identique à la source, limites de longueur (titre 300, description 5 000, objectif 300 caractères), balises HTML retirées avec `_sanitize_model_text` et champs vides refusés. Un JSON invalide est retenté une fois ; sinon une erreur française explicite est renvoyée. Si la langue du cours est déjà la langue cible, le serveur renvoie le contenu original sans appel. Le total envoyé est limité à 5 000 caractères. Le client Groq applique son délai de 20 s et ses retries 429/5xx ; les erreurs 401/403, 404 (`GROQ_MODEL`), 429, timeout et sortie invalide sont traduites en messages français distincts.

Les traductions sont mises en cache dans la collection MongoDB `course_translations`, avec la clé unique `(course_id, lang, course_updated_at)` créée par `python manage.py create_course_indexes`. Une modification, un changement de statut ou une suppression de cours purge les entrées correspondantes ; la clé inclut aussi `updated_at`, ce qui ignore automatiquement toute entrée antérieure. Le cache n'est jamais bloquant : une panne de cache laisse la traduction fonctionner.

Côté interface, le bloc traduit porte `lang` (et `dir="rtl"` pour l'arabe), le statut utilise `aria-live`, les erreurs `role="alert"`, le focus clavier est géré et les contrôles font au moins 44 px. Le JavaScript inline écrit toutes les valeurs avec `textContent`, jamais `innerHTML` ; les balises HTML renvoyées par le modèle sont retirées côté serveur et le texte restant est rendu comme du texte. Le bouton **Afficher l'original** masque la traduction sans recharger la page.

Les tests mockent HTTP et couvrent le succès `fr` → `en` et `fr` → `ar`, le cache (aucun second appel), le JSON invalide puis retry, un nombre d'objectifs incorrect, l'injection de prompt traitée comme donnée, une charge XSS nettoyée et rendue via `textContent`, la clé absente, les 401/403/404, le 429 avec retry Groq, le timeout, la langue invalide, le brouillon en 404, le CSRF, le limiteur, la limite de longueur, l'invalidation du cache et la journalisation `ai_logs`.

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

La configuration `.coveragerc` mesure le module `apps/courses` et exclut ses tests. La suite couvre les formulaires, le dépôt MongoDB, les services, une matrice explicite de permissions (anonyme, étudiant, instructeur propriétaire, instructeur non propriétaire, administrateur), les routes, les cas limites (ObjectId invalide, brouillons privés, panne MongoDB, CSRF, suppression en GET), les téléversements d'images (formats, taille, faux fichiers, remplacement et suppression de fichiers, placeholder), l'IA (succès, délai, clé absente, réponse invalide, stockage), le lancement web de l'évaluation (matrice de permissions, CSRF, 409, 429, clé absente, progression, succès, échec conservant l'ancien rapport, écriture atomique), la traduction (HTTP Groq mocké, cache MongoDB, erreurs 400/401/403/404/429/timeout, JSON invalide avec retry, XSS nettoyée, limiteur, journalisation `ai_logs`) et les commandes de gestion. Les tests utilisent la base dédiée `e_learning_test`, supprimée à la fin de chaque classe de tests ; la base de développement n'est jamais utilisée. La CI GitHub Actions démarre MongoDB, exécute les tests, vérifie un seuil de couverture de 90 %, lance `check --deploy` avec les paramètres de production et construit l'image Docker.

Pour les contrôles de déploiement, configurer les variables de production puis exécuter `python manage.py check --deploy`. Les preuves d'accessibilité et d'évaluation IA sont documentées dans `docs/accessibilite.md` et `docs/ia-evaluation.md`.

## Limites connues

- Les mentors et leurs tableaux de bord restent des exemples en mémoire. Les cours, quiz et tentatives sont persistés dans MongoDB ; les statistiques de quiz sont calculées à partir des tentatives enregistrées.
- Les comptes métier MongoDB ne sont pas reliés à `django.contrib.auth`; l'authentification Django native et son panneau d'administration ne sont pas disponibles.
- L'application `enrollments`, la progression persistante, ainsi qu'une interface MongoDB d'administration restent à concevoir avec leurs règles d'accès avant leur implémentation.
- `db.sqlite3` est conservé dans l'arborescence locale pour préserver les données préexistantes, mais n'est plus configuré ni utilisé par l'application. `.gitignore` ignore les fichiers SQLite locaux. Il n'a pas été supprimé.
- Les cours ne gèrent pas encore l'inscription ni la progression ; l'application `enrollments` reste à concevoir. Les images sont stockées sur le système de fichiers local (aucun stockage objet ni CDN n'est configuré).
- L'assistant IA des cours dépend de Groq (`GROQ_API_KEY`). Les tests mockent HTTP ; sans clé, la génération est indisponible mais le CRUD fonctionne.

## Crédits des thèmes

La landing page utilise le thème Skilline de [ThemeWagon](https://themewagon.com/themes/skilline/), conçu par [Irvan Moses et mhaecal](https://github.com/mhaecal/) sous licence MIT. Les sources et attributions du thème de back-office sont conservées dans `backoffice/`.
