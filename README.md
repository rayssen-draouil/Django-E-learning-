# SkillForge e-learning

Application d'apprentissage construite avec Django 5.2.18, PyMongo et MongoDB. Les comptes applicatifs et les sessions Django sont stockés dans MongoDB. Le projet n'utilise plus de base relationnelle.

## Architecture

```text
config/                 Réglages, URLs, points d'entrée WSGI/ASGI, connexion MongoDB
apps/users/             Comptes, formulaires, règles métier, dépôt MongoDB, sessions et permissions
apps/courses/           Pages de cours et routes du domaine
apps/mentors/           Pages de mentors et routes du domaine
apps/quizzes/           Pages de quiz et routes du domaine
apps/support/           Tickets de support, échanges, notifications, FAQ assistée et tableau de bord admin
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

3. Copier `.env.example` vers `.env` et définir les variables dans l'environnement du processus. Le projet ne charge pas automatiquement `.env`.
4. Démarrer MongoDB et préparer les index :

   ```powershell
   python manage.py create_user_indexes
   ```

5. Lancer le serveur :

   ```powershell
   python manage.py runserver
   ```

Le module Support est disponible sur `/support/`. Après le démarrage de MongoDB, préparer aussi ses index avec `python manage.py create_support_indexes`. Les pièces jointes autorisées (PNG, JPG, WEBP, GIF, PDF et TXT, 5 Mo maximum) sont conservées dans `MEDIA_ROOT/support/` et ne sont accessibles qu'aux participants autorisés au ticket. Les suggestions d'assistance utilisent une FAQ locale et ne nécessitent aucun service IA externe.

Le module couvre le cycle complet d'une demande : création, consultation de l'historique, modification du titre, de la description et de la catégorie, suppression par l'étudiant sur un ticket vierge ou par un administrateur, et clôture comme résolu. Les priorités (faible, normale, élevée, urgente) et les statuts (ouvert, en cours, en attente, résolu) sont pilotés par le support, avec attribution du ticket à un administrateur et conservation de l'historique complet des changements de statut. Chaque ticket dispose d'un fil de messages entre l'étudiant et le support, avec pièces jointes justificatives.

Routes du module :

| Route | Accès | Usage |
| --- | --- | --- |
| `/support/` | Étudiant | File des demandes de l'étudiant ou file complète pour un administrateur. |
| `/support/new/` | Étudiant | Création d'une demande ou réclamation. |
| `/support/solutions/` | Étudiant | Recherche d'une solution dans la base de connaissances. |
| `/support/<id>/` | Participant | Détail, historique, conversation et actions de suivi. |
| `/support/<id>/delete/` | Participant | Suppression d'un ticket autorisé (POST). |
| `/support/dashboard/` | Administrateur | Tableau de bord du support. |
| `/support/faq/` | Administrateur | Gestion de la base de connaissances (création, modification, publication). |
| `/support/notifications/` | Utilisateur | Centre de notifications. |

### Répartition Front Office / Back Office

| Front Office — étudiant | Back Office — administrateur / agent support |
| --- | --- |
| Créer une réclamation | Gérer toutes les réclamations |
| Consulter ses tickets et leur historique | Filtrer la file (statut, priorité, catégorie, assignataire, recherche texte), trier par priorité et paginer |
| Échanger des messages et joindre des fichiers | Répondre, changer le statut et la priorité, attribuer un ticket, ajouter des notes internes |
| Rechercher une solution dans la base de connaissances | Gérer la FAQ (questions, réponses, mots-clés, publication) et analyser les statistiques |

Les notes internes sont réservées à l'équipe support : elles ne sont jamais-chargées dans le contexte d'un ticket étudiant, et leur suppression est réservée à leur auteur. La file du Back Office se filtre côté serveur (requête MongoDB) et non en mémoire, avec une pagination de 20 tickets par page ; le tri par priorité utilise une agrégation qui ordonne urgent, élevée, normale puis faible.

Le tableau de bord du support (`/support/dashboard/`, réservé aux administrateurs) expose le nombre de tickets ouverts et résolus, les tickets en cours et en attente, la file non assignée, le temps moyen et médian de résolution, la répartition par catégorie, par priorité et par statut, ainsi que les problèmes récurrents. Ces derniers sont détectés en regroupant les tickets par thème métier (vidéo, quiz, mentorat, compte, accès aux cours, progression, paiement, application) puis, à défaut, par mots-clés significatifs, ce qui permet de relier plusieurs formulations d'un même problème et de compter les étudiants impactés.

Les notifications sont enregistrées dans la collection `support_notifications` à chaque événement utile : nouveau ticket, nouvelle réponse, changement de statut et attribution. Le badge de la barre de navigation affiche le nombre de notifications non lues, la lecture d'un ticket marque ses notifications comme lues et le centre de notifications permet de tout marquer d'un clic. Un ticket supprimé emporte avec lui ses messages et ses notifications.

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

Les données du support sont réparties dans cinq collections : `support_tickets` (tickets, priorité, statut, assignation et historique des statuts), `support_messages` (conversation et pièces jointes), `support_notifications` (notifications adressées à un utilisateur), `support_notes` (notes internes du support) et `support_faq` (base de connaissances). La commande `create_support_indexes` prépare les index de consultation par étudiant, par file, par catégorie, par conversation, par notifications et par notes ; la commande `seed_support_faq` charge les solutions par défaut lorsque la base de connaissances est vide.

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
.\.venv\Scripts\python.exe manage.py test apps.support
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
