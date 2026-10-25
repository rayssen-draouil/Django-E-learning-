# Accessibilité — SkillForge

Ce document décrit les garanties d'accessibilité du catalogue et du back office des cours, les tests automatisés associés et la procédure d'audit Lighthouse.

## Périmètre

Pages auditées :

- `/` — page d'accueil publique ;
- `/users/cours/` — catalogue public ;
- `/users/cours/<slug>/` — fiche d'un cours publié ;
- `/users/backoffice/cours/create/` — formulaire de création (instructeur ou administrateur connecté) ;
- pages d'erreur 403, 404 et 500 ;
- pages de gestion des cours (liste, détail, édition, suppression).

## Garanties mises en place

### Structure et navigation

- `lang="fr"` sur chaque document.
- Un seul `<h1>` par page, complété par des titres `<h2>` hiérarchisés.
- Lien d'évitement « Aller au contenu principal » en début de page.
- Points de repère sémantiques : `header`, `nav`, `main`, `footer`, `aside`.
- Navigation principale et pagination identifiées par `aria-label`.
- L'état de la page courante est signalé avec `aria-current="page"`.

### Formulaires

- Chaque champ visible possède un `<label for="...">` associé.
- Les champs obligatoires utilisent la validation native du navigateur (`required`) sans `novalidate` ; les contraintes `minlength`, `maxlength`, `min` et `max` reprennent les règles serveur.
- Les erreurs sont annoncées avec `role="alert"` et reliées au champ via `aria-describedby`.
- Un champ en erreur reçoit `aria-invalid="true"`.
- Les champs obligatoires affichent « Champ obligatoire. » ; « Champ facultatif. » n'apparaît jamais sous un champ obligatoire.
- Les actions IA utilisent des `<button type="button">` et des zones `aria-live` pour annoncer les résultats sans déplacer le focus.

### Images

- Chaque `<img>` possède un attribut `alt`.
- Les images de cours utilisent `image_alt` lorsqu'il est renseigné, sinon le titre du cours.
- Les images purement décoratives utilisent `alt=""`.

### Interaction et affichage

- Les boutons, sélecteurs, champs de filtre et liens de navigation ont une hauteur minimale de `2.75rem` (44 px à la taille de police par défaut).
- Les styles de focus visibles sont conservés (`:focus-visible`, contour de 3 px).
- Le texte de contenu utilise une taille de base de 16 px.
- Les messages de succès et d'erreur sont exposés via `role="status"` ou `role="alert"`.

## Tests automatisés

```bash
python manage.py test apps.courses.tests.test_accessibility
python manage.py test apps.courses.tests.test_error_pages
```

Les tests d'accessibilité vérifient, sur le HTML réellement rendu :

- l'unicité du `<h1>` ;
- la présence d'un texte alternatif sur toutes les images ;
- l'association de chaque champ visible à un label ;
- l'absence de `novalidate` sur le formulaire de cours ;
- la présence de `required`, `minlength`, `maxlength`, `min` et `max` ;
- l'absence de « Champ facultatif. » sous les champs obligatoires ;
- la hauteur minimale de 44 px des contrôles du catalogue.

Les tests des pages d'erreur vérifient le code HTTP, le contenu français, l'unicité du `<h1>`, le lien de retour à l'accueil et l'absence de fuite de trace technique lorsque `DEBUG=False`.

## Audit Lighthouse

### Prérequis

1. Démarrer l'application :

   ```bash
   python manage.py runserver
   ```

2. Ouvrir Chrome et se connecter avec un compte instructeur ou administrateur pour auditer les pages du back office.

### Via Chrome DevTools

1. Ouvrir la page à auditer, par exemple `http://127.0.0.1:8000/users/cours/`.
2. Ouvrir DevTools (`F12`), puis l'onglet **Lighthouse**.
3. Sélectionner le mode **Navigation**, l'appareil **Mobile** puis **Desktop**, et cocher la catégorie **Accessibility**.
4. Lancer l'analyse.
5. Objectif : score **100**. Tout point d'audit en échec doit être corrigé ou documenté.

Pour le formulaire de cours, se connecter d'abord dans le même profil Chrome, puis auditer `http://127.0.0.1:8000/users/backoffice/cours/create/`.

### Via la CLI

Avec Node.js installé :

```bash
npx lighthouse http://127.0.0.1:8000/users/cours/ \
  --only-categories=accessibility \
  --chrome-flags="--headless=new" \
  --output=html \
  --output-path=./lighthouse-cours.html
```

Répéter pour les autres pages publiques :

```bash
npx lighthouse http://127.0.0.1:8000/ --only-categories=accessibility --chrome-flags="--headless=new"
npx lighthouse http://127.0.0.1:8000/cette-page-nexiste-pas/ --only-categories=accessibility --chrome-flags="--headless=new"
```

La CLI ne partage pas la session de connexion du navigateur. Pour le back office, préférer DevTools après connexion, ou fournir un cookie de session valide à Lighthouse.

### Vérifications manuelles complémentaires

- Parcourir chaque page uniquement au clavier : le focus doit rester visible et suivre un ordre logique.
- Vérifier le fonctionnement à 200 % de zoom et avec une largeur de 320 px.
- Tester avec un lecteur d'écran (NVDA, VoiceOver ou TalkBack) : titres, labels, erreurs et messages dynamiques doivent être annoncés.
- Vérifier les contrastes des textes, boutons et badges ; Lighthouse ne couvre qu'une partie des critères WCAG.
- Vérifier que les animations respectent `prefers-reduced-motion` lorsqu'elles sont ajoutées.
