# Skilline - Landing Page

#### Preview

 - [Demo](https://themewagon.github.io/skilline/)

#### Download
 - [Download from ThemeWagon](https://themewagon.com/themes/skilline/)

## Getting Started

1. Clone Repository
```
git clone https://github.com/themewagon/skilline.git
```

## Routes — Cours (namespace `users`)

| Page | URL | Nom d'URL |
|---|---|---|
| Liste des cours | `/users/cours/` | `users:courses` |
| Créer un cours | `/users/backoffice/cours/nouveau/` | `users:course_create` |
| Créer (alias) | `/users/cours/nouveau/` et `/users/cours/create/` → redirigent vers `/users/backoffice/cours/nouveau/` | — |
| Détail d'un cours | `/users/cours/<slug>/` (ex. `/users/cours/python-pour-debutants/`) | `users:course_detail` |
| Modifier | `/users/cours/<slug>/modifier/` | `users:course_update` |
| Supprimer | `/users/cours/<slug>/supprimer/` | `users:course_delete` |
```
uv run python manage.py seed_demo
```


## Author 
```
Design and code is completely written by irvanmoses & mhaecal and development team. 
```

## License

 - Code is Copyright &copy; <a href="https://github.com/mhaecal/" target="_blank">mhaecal</a>
 - Licensed cover under [MIT]
 - Distributed by <a href="https://themewagon.com" target="_blank">ThemeWagon</a>
