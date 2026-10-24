# SkillForge e-learning

Application d'apprentissage construite avec Django 5.2.18, PyMongo et MongoDB. Les comptes applicatifs et les sessions Django sont stockés dans MongoDB. Le projet n'utilise plus de base relationnelle.

## Architecture

```text
config/                 Réglages, URLs, points d'entrée WSGI/ASGI, connexion MongoDB
apps/users/             Comptes, formulaires, règles métier, dépôt MongoDB, sessions et permissions
apps/courses/           Pages de cours et routes du domaine
apps/mentors/           Pages de mentors et routes du domaine
apps/quizzes/           Pages de quiz et routes du domaine
templates/              Templates globaux front-office et tableaux de bord
static/                 CSS et images servis par Django
backoffice/              Sources autonomes du thème HTML/Gulp
index.html, css/         Démonstration statique Skilline conservée séparément
```

Les routes publiques existantes sont conservées, y compris les noms `users:*` utilisés par les templates. Les cours et mentors affichent encore du contenu d'exemple en mémoire. Les quiz sont stockés dans MongoDB via PyMongo dans les collections `quizzes` et `quiz_attempts` ; les auteurs peuvent gérer leurs quiz et les étudiants répondre aux quiz publiés et consulter leurs résultats. Il n'y a pas encore d'application `enrollments` car aucun parcours d'inscription ou de progression persistant n'existe.

Les templates globaux et les ressources du thème gardent leurs chemins actuels afin de préserver les liens existants. `backoffice/` et `index.html` sont des sources statiques autonomes, et ne sont pas les templates rendus par Django.

## Prérequis et démarrage

1. Python compatible avec Django 5.2 et MongoDB.
2. Créer un environnement virtuel et installer les dépendances :

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Copier `.env.example` vers `.env` puis renseigner les variables nécessaires. Les variables du processus ont priorité sur celles du fichier `.env` à la racine du projet.
4. Démarrer MongoDB et préparer les index :

   ```powershell
   python manage.py create_user_indexes
   ```

5. Lancer le serveur :

   ```powershell
   python manage.py runserver
   ```

Le module de paramètres par défaut est `config.settings` et charge les paramètres de développement. En production, définir `DJANGO_SETTINGS_MODULE=config.settings.production`, fournir une clé secrète, les hôtes autorisés et une URI MongoDB sécurisée. Le proxy ou serveur doit terminer HTTPS lorsque la redirection SSL est activée.

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
| `DJANGO_SECURE_SSL_REDIRECT` | Redirection HTTPS en production ; active par défaut. |
| `DJANGO_SECURE_HSTS_SECONDS` | Durée HSTS ; défaut `31536000`. |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | Inclut les sous-domaines dans HSTS ; désactivé par défaut. |
| `DJANGO_SECURE_HSTS_PRELOAD` | Active l'option de préchargement HSTS ; désactivée par défaut. |

## Données, sessions et rôles

Les documents utilisateurs restent dans la collection `users`; les mots de passe sont hachés et l'adresse e-mail est protégée par un index unique. La commande `create_user_indexes` prépare cet index et l'index TTL de la collection de sessions. MongoDB supprime les sessions expirées via TTL ; le backend vérifie aussi l'expiration au chargement. Une indisponibilité MongoDB interrompt les opérations de session au lieu de simuler une session vide.

Les rôles applicatifs sont `student`, `instructor` et `admin`. Les tableaux de bord actuels sont réservés aux étudiants. L'administration Django native (`/admin/`) a été retirée avec les dépendances relationnelles ; aucune interface web d'administration MongoDB n'est fournie. Le provisionnement de comptes privilégiés doit Ãªtre effectué par un outil d'administration contrôlé et non exposé publiquement.

Les messages Django sont stockés dans des cookies signés pour éviter une dépendance à la base de sessions relationnelle. Les fonctions de hachage et de validation des mots de passe fournies par Django restent utilisées ; elles ne nécessitent pas les tables Django.

## Sauvegarde et restauration

Sauvegarder la base configurée, qui contient les comptes et les sessions :

```powershell
mongodump --uri "$env:MONGODB_URI" --db "$env:MONGODB_DATABASE" --out .\backups
```

Restaurer avec `mongorestore` vers une instance contrôlée et vérifier les collections `users` et `django_sessions`. Les sessions peuvent Ãªtre invalidées après restauration si les clés de signature ou les paramètres de sécurité ont changé. Protéger les sauvegardes comme des données sensibles et les stocker hors du dépôt.

## Tests et vérifications

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test
```

Pour les contrôles de déploiement, configurer les variables de production puis exécuter `python manage.py check --deploy`.

## Limites connues

- Les cours et mentors ainsi que leurs tableaux de bord restent des exemples en mémoire. Les quiz et tentatives sont persistés dans MongoDB ; les statistiques de quiz sont calculées à partir des tentatives enregistrées.
- Les comptes métier MongoDB ne sont pas reliés à `django.contrib.auth`; l'authentification Django native et son panneau d'administration ne sont pas disponibles.
- L'application `enrollments`, la progression persistante, ainsi qu'une interface MongoDB d'administration restent à concevoir avec leurs règles d'accès avant leur implémentation.
- `db.sqlite3` est conservé dans l'arborescence locale pour préserver les données préexistantes, mais n'est plus configuré ni utilisé par l'application. `.gitignore` ignore les fichiers SQLite locaux ; sans métadonnées Git, son état de suivi antérieur ne peut pas Ãªtre vérifié. Il n'a pas été supprimé.
- Le dépôt fourni dans l'espace de travail ne contient pas de dossier `.git`, donc le suivi des fichiers et les modifications locales antérieures ne peuvent pas Ãªtre vérifiés ici.

## Crédits des thèmes

La landing page utilise le thème Skilline de [ThemeWagon](https://themewagon.com/themes/skilline/), conÃ§u par [Irvan Moses et mhaecal](https://github.com/mhaecal/) sous licence MIT. Les sources et attributions du thème de back-office sont conservées dans `backoffice/`.

## Génération de quiz avec Gemini

Définir GEMINI_API_KEY dans le fichier `.env` ou dans l'environnement du processus à partir d'une clé Google AI Studio. Le SDK Python google-genai est installé par requirements.txt. GEMINI_MODEL sélectionne le modèle compatible avec la sortie JSON structurée (valeur par défaut : gemini-3.8-flash) ; GEMINI_TIMEOUT_SECONDS règle le délai d'attente. La limite est de cinq générations par utilisateur et par heure par défaut (QUIZ_AI_GENERATION_LIMIT et QUIZ_AI_GENERATION_WINDOW_SECONDS). La clé reste côté serveur. La prévisualisation n'écrit rien dans MongoDB avant confirmation.

