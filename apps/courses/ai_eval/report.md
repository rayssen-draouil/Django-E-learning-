# AI Evaluation Report

> **Mode : `--eval` — évaluation réelle via l'API Groq.**

Dataset size: **36** entries

Coverage (answered): **36/36** (100.0 %)

API errors: **0** (429: 0, timeout: 0, other: 0)

Invalid JSON/enum model errors: **0**

Mean latency (all attempts): **841 ms** | p95: **1420 ms**

> Classification metrics, per-class support and confusion matrices are computed **only over answered cases**. API errors and invalid model outputs are excluded and reported separately.

## Category classification

Accuracy: **86.1 %** | Macro-F1: **0.863**

+-------------+-----------+--------+-------+---------+
| Class       | Precision | Recall | F1    | Support |
+-------------+-----------+--------+-------+---------+
| programming | 0.667     | 1.000  | 0.800 | 6       |
| web         | 0.800     | 0.667  | 0.727 | 6       |
| data        | 1.000     | 0.833  | 0.909 | 6       |
| design      | 1.000     | 0.833  | 0.909 | 6       |
| business    | 0.857     | 1.000  | 0.923 | 6       |
| other       | 1.000     | 0.833  | 0.909 | 6       |
+-------------+-----------+--------+-------+---------+

## Level classification

Accuracy: **66.7 %** | Macro-F1: **0.652**

+--------------+-----------+--------+-------+---------+
| Class        | Precision | Recall | F1    | Support |
+--------------+-----------+--------+-------+---------+
| beginner     | 0.733     | 0.846  | 0.786 | 13      |
| intermediate | 0.600     | 0.600  | 0.600 | 15      |
| advanced     | 0.667     | 0.500  | 0.571 | 8       |
+--------------+-----------+--------+-------+---------+

## Matrice de confusion (catégories)

+-------------+-------------+-----+------+--------+----------+-------+
| true \ pred | programming | web | data | design | business | other |
+-------------+-------------+-----+------+--------+----------+-------+
| programming | 6           | 0   | 0    | 0      | 0        | 0     |
| web         | 2           | 4   | 0    | 0      | 0        | 0     |
| data        | 1           | 0   | 5    | 0      | 0        | 0     |
| design      | 0           | 1   | 0    | 5      | 0        | 0     |
| business    | 0           | 0   | 0    | 0      | 6        | 0     |
| other       | 0           | 0   | 0    | 0      | 1        | 5     |
+-------------+-------------+-----+------+--------+----------+-------+

## Matrice de confusion (niveaux)

+--------------+----------+--------------+----------+
| true \ pred  | beginner | intermediate | advanced |
+--------------+----------+--------------+----------+
| beginner     | 11       | 2            | 0        |
| intermediate | 4        | 9            | 2        |
| advanced     | 0        | 4            | 4        |
+--------------+----------+--------------+----------+

## Analyse

Les métriques de classification sont calculées uniquement sur les cas répondus ; les erreurs API (429, timeout, réseau) et les sorties JSON/enum invalides sont exclues et comptées séparément. La couverture indique le nombre de cas répondus sur le total du jeu de données.

**Points forts :** le JSON structuré, le cache court et le repli sans clé évitent d'écrire n'importe quoi dans le formulaire. Les classes « design » et « business » sont en général bien séparées du code.

**Confusions fréquentes :** `web` vs `programming` (Django, API, full-stack) ; `data` vs `programming` lorsque Python/Pandas domine le titre ; `other` absorbe parfois des soft skills proches du `business`.

**Limites :** dépendance à Groq et à la qualité du titre/description ; les tags ne sont pas évalués ; la latence réseau varie. `--mock` produit des scores parfaits (aucune inférence). Un run réel exige `GROQ_API_KEY` et `--eval` ; `--delay` ralentit les appels pour respecter les quotas Groq (2,5 s par défaut entre deux entrées).
