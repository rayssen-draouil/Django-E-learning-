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

Les parcours d'apprentissage sont enregistrés dans la collection MongoDB `learning_paths`. Les inscriptions et étapes terminées sont enregistrées dans `learning_path_enrollments` : les étudiants s'inscrivent aux parcours publiés, marquent leurs étapes comme terminées et consultent leur progression sur `/parcours/mes-parcours/`. Les formateurs voient le nombre d'inscrits depuis `/parcours/manage/` et le détail d'avancement de leurs parcours. Le tableau de bord `/parcours/manage/statistics/` présente les inscriptions, le taux de parcours terminés, la progression moyenne et les parcours les plus suivis ; les formateurs ne voient que leurs propres parcours et l'administrateur dispose de la vue globale. Le taux de complétion compte les inscriptions ayant terminé toutes les étapes ; la progression moyenne est une moyenne par inscription. Les visiteurs consultent, recherchent, filtrent par thème/niveau et trient les parcours publiés sur `/parcours/`; les formateurs gèrent les leurs et les administrateurs peuvent tous les gérer depuis `/parcours/manage/`, avec recherche, filtre par statut et tri. Un parcours contient au moins deux cours sélectionnés dans le catalogue de démonstration de l'application, dans l'ordre des étapes. Les parcours publiés peuvent être téléchargés en PDF depuis leur page publique ; les formateurs et administrateurs peuvent également exporter les parcours qu'ils sont autorisés à gérer, y compris les brouillons. Le PDF présente le titre, la description et les étapes ordonnées ; l'export privé de gestion inclut en plus les apprenants inscrits et leur progression, sans exposer ces données dans le téléchargement public. Les filtres de thème et niveau sont enregistrés sur chaque parcours. Depuis `/parcours/manage/`, les formateurs et administrateurs peuvent demander à Ollama une proposition de parcours fondée sur un objectif, un métier et un niveau ; les cours proposés sont limités au catalogue et le brouillon doit être vérifié puis enregistré par l'utilisateur. La progression des cours du catalogue de démonstration est déclarée manuellement par l'apprenant ; elle n'est pas déduite d'activités de cours persistées.

Les templates globaux et les ressources du thème gardent leurs chemins actuels afin de préserver les liens existants. `backoffice/` et `index.html` sont des sources statiques autonomes, et ne sont pas les templates rendus par Django.

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
   ```

5. Installer Ollama, démarrer son service et télécharger le modèle choisi (le défaut est `llama3.2`) :

   ```powershell
   ollama pull llama3.2
   ```

   Configurez `OLLAMA_BASE_URL`, `OLLAMA_MODEL` et `OLLAMA_TIMEOUT_SECONDS` dans `.env` si nécessaire.

6. Lancer le serveur :

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
| `OLLAMA_BASE_URL` | URL du service Ollama local ; défaut `http://localhost:11434`. |
| `OLLAMA_MODEL` | Nom du modèle Ollama à utiliser ; défaut `llama3.2`. Le modèle doit être téléchargé localement. |
| `OLLAMA_TIMEOUT_SECONDS` | Délai maximal de réponse Ollama en secondes ; défaut `90`. |
| `USERS_ADMIN_BOOTSTRAP_TOKEN` | Jeton facultatif du service de provisionnement d'un compte avec rôle admin. |
| `DJANGO_SECURE_SSL_REDIRECT` | Redirection HTTPS en production ; active par défaut. |
| `DJANGO_SECURE_HSTS_SECONDS` | Durée HSTS ; défaut `31536000`. |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | Inclut les sous-domaines dans HSTS ; désactivé par défaut. |
| `DJANGO_SECURE_HSTS_PRELOAD` | Active l'option de préchargement HSTS ; désactivée par défaut. |

## Données, sessions et rôles

Les documents utilisateurs restent dans la collection `users`; les mots de passe sont hachés et l'adresse e-mail est protégée par un index unique. La commande `create_user_indexes` prépare les index requis, notamment ceux des collections `learning_paths` et `learning_path_enrollments` et l'index TTL de la collection de sessions. MongoDB supprime les sessions expirées via TTL ; le backend vérifie aussi l'expiration au chargement. Une indisponibilité MongoDB interrompt les opérations de session au lieu de simuler une session vide.

Les rôles applicatifs sont `student`, `instructor` et `admin`. Les tableaux de bord actuels sont réservés aux étudiants. L'administration Django native (`/admin/`) a été retirée avec les dépendances relationnelles ; aucune interface web d'administration MongoDB n'est fournie. Le provisionnement de comptes privilégiés doit être effectué par un outil d'administration contrôlé et non exposé publiquement.

Les messages Django sont stockés dans des cookies signés pour éviter une dépendance à la base de sessions relationnelle. Les fonctions de hachage et de validation des mots de passe fournies par Django restent utilisées ; elles ne nécessitent pas les tables Django.

## Sauvegarde et restauration

Sauvegarder la base configurée, qui contient les comptes et les sessions :

```powershell
mongodump --uri "$env:MONGODB_URI" --db "$env:MONGODB_DATABASE" --out .\backups
```

Restaurer avec `mongorestore` vers une instance contrôlée et vérifier les collections `users` et `django_sessions`. Les sessions peuvent être invalidées après restauration si les clés de signature ou les paramètres de sécurité ont changé. Protéger les sauvegardes comme des données sensibles et les stocker hors du dépôt.

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
- `db.sqlite3` est conservé dans l'arborescence locale pour préserver les données préexistantes, mais n'est plus configuré ni utilisé par l'application. `.gitignore` ignore les fichiers SQLite locaux ; sans métadonnées Git, son état de suivi antérieur ne peut pas être vérifié. Il n'a pas été supprimé.
- Le dépôt fourni dans l'espace de travail ne contient pas de dossier `.git`, donc le suivi des fichiers et les modifications locales antérieures ne peuvent pas être vérifiés ici.

## Crédits des thèmes

La landing page utilise le thème Skilline de [ThemeWagon](https://themewagon.com/themes/skilline/), conçu par [Irvan Moses et mhaecal](https://github.com/mhaecal/) sous licence MIT. Les sources et attributions du thème de back-office sont conservées dans `backoffice/`.
