"""Management command: ai_report

Displays AI assistant metrics from the ai_logs collection and optionally runs
a batch evaluation of suggest_meta against the labelled dataset.

Usage:
    python manage.py ai_report              # live metrics from MongoDB
    python manage.py ai_report --mock       # dry-run with fake predictions (tests)
    python manage.py ai_report --eval       # run the evaluation entry point on all
                                            # dataset entries (requires GROQ_API_KEY)
    python manage.py ai_report --eval --delay 0.5  # custom delay between entries
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Callable

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from pymongo.errors import PyMongoError

from apps.courses.ai import (
    ALLOWED_CATEGORIES,
    ALLOWED_LEVELS,
    AIError,
    AIProviderError,
    AIResponseError,
    AITimeoutError,
    get_ai_stats,
    get_groq_api_key,
    get_groq_model,
    suggest_meta_for_evaluation,
)
from apps.courses.ai_eval.metrics import compute_metrics

_DATASET_PATH = Path(__file__).resolve().parent.parent.parent / "ai_eval" / "dataset.json"
_REPORT_PATH = Path(__file__).resolve().parent.parent.parent / "ai_eval" / "report.md"
_REPORT_JSON_PATH = Path(__file__).resolve().parent.parent.parent / "ai_eval" / "report.json"

CATEGORY_CLASSES = list(ALLOWED_CATEGORIES)
LEVEL_CLASSES = list(ALLOWED_LEVELS)
DEFAULT_EVAL_DELAY = 2.5


class EvaluationError(Exception):
    """Base class for evaluation failures shared by the command and the web task."""

    code = "evaluation_error"


class EvaluationNotConfiguredError(EvaluationError):
    code = "missing_key"


class EvaluationDatasetError(EvaluationError):
    code = "invalid_dataset"

ANALYSIS_TEXT = (
    "Les métriques de classification sont calculées uniquement sur les cas répondus ; "
    "les erreurs API (429, timeout, réseau) et les sorties JSON/enum invalides sont "
    "exclues et comptées séparément. La couverture indique le nombre de cas répondus "
    "sur le total du jeu de données.\n\n"
    "**Points forts :** le JSON structuré, le cache court et le repli sans clé évitent "
    "d'écrire n'importe quoi dans le formulaire. Les classes « design » et « business » "
    "sont en général bien séparées du code.\n\n"
    "**Confusions fréquentes :** `web` vs `programming` (Django, API, full-stack) ; "
    "`data` vs `programming` lorsque Python/Pandas domine le titre ; "
    "`other` absorbe parfois des soft skills proches du `business`.\n\n"
    "**Limites :** dépendance à Groq et à la qualité du titre/description ; "
    "les tags ne sont pas évalués ; la latence réseau varie. "
    "`--mock` produit des scores parfaits (aucune inférence). "
    "Un run réel exige `GROQ_API_KEY` et `--eval` ; `--delay` ralentit les appels "
    "pour respecter les quotas Groq (2,5 s par défaut entre deux entrées).\n"
)


@dataclass
class EvalOutcome:
    """Answered cases and error counts for one evaluation run."""

    cat_true: list[str]
    cat_pred: list[str]
    lvl_true: list[str]
    lvl_pred: list[str]
    latencies_ms: list[float]
    answered: int
    rate_limit_errors: int
    timeout_errors: int
    other_api_errors: int
    invalid_model_errors: int
    total: int

    @property
    def api_errors(self) -> int:
        return self.rate_limit_errors + self.timeout_errors + self.other_api_errors


@dataclass
class EvaluationResult:
    outcome: EvalOutcome
    metrics_cat: dict | None
    metrics_lvl: dict | None


class _NullOutput:
    def write(self, message: str) -> None:
        pass


def _load_dataset() -> list[dict]:
    """Load and return the labelled evaluation dataset."""
    try:
        dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationDatasetError("Le jeu d'évaluation est illisible.") from exc
    if not isinstance(dataset, list) or not dataset:
        raise EvaluationDatasetError("Le jeu d'évaluation est vide ou mal formé.")
    return dataset


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a simple ASCII table."""
    widths = [max(len(h), max((len(str(r[i])) for r in rows), default=0)) for i, h in enumerate(headers)]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    def row_line(cells):
        return "| " + " | ".join(str(c).ljust(w) for c, w in zip(cells, widths)) + " |"
    lines = [sep, row_line(headers), sep]
    for row in rows:
        lines.append(row_line(row))
    lines.append(sep)
    return "\n".join(lines)


def _build_mock_predictions(dataset: list[dict]) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return perfect mock predictions that match ground truth (for tests)."""
    cat_true = [e["true_category"] for e in dataset]
    lvl_true = [e["true_level"] for e in dataset]
    return cat_true, cat_true[:], lvl_true, lvl_true[:]


def _format_confusion(matrix: list[list[int]], classes: list[str]) -> str:
    headers = ["true \\ pred"] + classes
    rows = [[classes[i]] + [str(v) for v in matrix[i]] for i in range(len(classes))]
    return _format_table(headers, rows)


def _run_eval(
    dataset: list[dict],
    stdout=None,
    *,
    delay: float = DEFAULT_EVAL_DELAY,
    progress_callback: Callable[[int, int], None] | None = None,
) -> EvalOutcome:
    """Call the unlimited evaluation entry point on every entry.

    Successful, validated answers contribute to the metrics. Provider errors
    (429, timeout, network) and invalid JSON/enum outputs are counted
    separately and never enter the metric lists.
    """
    output = stdout if stdout is not None else _NullOutput()

    outcome = EvalOutcome(
        cat_true=[],
        cat_pred=[],
        lvl_true=[],
        lvl_pred=[],
        latencies_ms=[],
        answered=0,
        rate_limit_errors=0,
        timeout_errors=0,
        other_api_errors=0,
        invalid_model_errors=0,
        total=len(dataset),
    )

    for i, entry in enumerate(dataset, 1):
        if i > 1 and delay > 0:
            time.sleep(delay)
        output.write(f"  [{i}/{len(dataset)}] {entry['id']} …")
        started = time.monotonic()
        try:
            result = suggest_meta_for_evaluation(entry["title"], entry.get("description", ""))
        except AITimeoutError:
            outcome.timeout_errors += 1
            output.write(" ERROR (timeout)\n")
        except AIProviderError as error:
            if error.status_code == 429:
                outcome.rate_limit_errors += 1
                output.write(" ERROR (HTTP 429)\n")
            else:
                outcome.other_api_errors += 1
                label = f"HTTP {error.status_code}" if error.status_code else "réseau"
                output.write(f" ERROR ({label})\n")
        except AIResponseError:
            outcome.invalid_model_errors += 1
            output.write(" ERROR (JSON/enum invalide)\n")
        except AIError:
            outcome.other_api_errors += 1
            output.write(" ERROR (API)\n")
        else:
            latency_ms = (time.monotonic() - started) * 1000
            outcome.latencies_ms.append(latency_ms)
            outcome.cat_true.append(entry["true_category"])
            outcome.cat_pred.append(result.get("category", ""))
            outcome.lvl_true.append(entry["true_level"])
            outcome.lvl_pred.append(result.get("level", ""))
            outcome.answered += 1
            output.write(f" OK ({latency_ms:.0f} ms)\n")
            if progress_callback is not None:
                progress_callback(i, len(dataset))
            continue
        outcome.latencies_ms.append((time.monotonic() - started) * 1000)
        if progress_callback is not None:
            progress_callback(i, len(dataset))

    return outcome


def _atomic_write_text(path: Path, content: str) -> None:
    """Write to a temporary file in the same directory, then replace atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _write_report(
    metrics_cat: dict | None,
    metrics_lvl: dict | None,
    outcome: EvalOutcome,
    report_path: Path,
    *,
    mocked: bool,
) -> None:
    """Write a markdown report to disk."""
    sorted_lat = sorted(outcome.latencies_ms)
    mean_lat = sum(sorted_lat) / len(sorted_lat) if sorted_lat else 0
    p95_lat = sorted_lat[max(0, math.ceil(len(sorted_lat) * 0.95) - 1)] if sorted_lat else 0
    answered_pct = outcome.answered / outcome.total * 100 if outcome.total else 0

    buf = StringIO()
    buf.write("# AI Evaluation Report\n\n")
    if mocked:
        buf.write(
            "> **Mode : `--mock` — évaluation simulée. Aucune requête n'a été envoyée "
            "à Groq et `GROQ_API_KEY` n'a pas été utilisée.**\n\n"
        )
    else:
        buf.write(
            "> **Mode : `--eval` — évaluation réelle via l'API Groq.**\n\n"
        )
    buf.write(f"Dataset size: **{outcome.total}** entries\n\n")
    buf.write(
        f"Coverage (answered): **{outcome.answered}/{outcome.total}** "
        f"({answered_pct:.1f} %)\n\n"
    )
    buf.write(
        f"API errors: **{outcome.api_errors}** "
        f"(429: {outcome.rate_limit_errors}, timeout: {outcome.timeout_errors}, "
        f"other: {outcome.other_api_errors})\n\n"
    )
    buf.write(
        f"Invalid JSON/enum model errors: **{outcome.invalid_model_errors}**\n\n"
    )
    buf.write(f"Mean latency (all attempts): **{mean_lat:.0f} ms** | p95: **{p95_lat:.0f} ms**\n\n")
    buf.write(
        "> Classification metrics, per-class support and confusion matrices are "
        "computed **only over answered cases**. API errors and invalid model outputs "
        "are excluded and reported separately.\n\n"
    )

    if metrics_cat is None:
        buf.write("## Category classification\n\nNo answered case: metrics are not available.\n\n")
    else:
        buf.write("## Category classification\n\n")
        buf.write(f"Accuracy: **{metrics_cat['accuracy'] * 100:.1f} %** | "
                  f"Macro-F1: **{metrics_cat['macro_f1']:.3f}**\n\n")

        cat_rows = []
        for cls, stats in metrics_cat["per_class"].items():
            cat_rows.append([
                cls,
                f"{stats['precision']:.3f}",
                f"{stats['recall']:.3f}",
                f"{stats['f1']:.3f}",
                str(stats["support"]),
            ])
        buf.write(_format_table(["Class", "Precision", "Recall", "F1", "Support"], cat_rows))
        buf.write("\n\n")

    if metrics_lvl is None:
        buf.write("## Level classification\n\nNo answered case: metrics are not available.\n\n")
    else:
        buf.write("## Level classification\n\n")
        buf.write(f"Accuracy: **{metrics_lvl['accuracy'] * 100:.1f} %** | "
                  f"Macro-F1: **{metrics_lvl['macro_f1']:.3f}**\n\n")
        lvl_rows = []
        for cls, stats in metrics_lvl["per_class"].items():
            lvl_rows.append([
                cls,
                f"{stats['precision']:.3f}",
                f"{stats['recall']:.3f}",
                f"{stats['f1']:.3f}",
                str(stats["support"]),
            ])
        buf.write(_format_table(["Class", "Precision", "Recall", "F1", "Support"], lvl_rows))
        buf.write("\n\n")

    if metrics_cat is None:
        buf.write("## Matrice de confusion (catégories)\n\nNon calculée : aucun cas répondu.\n\n")
    else:
        buf.write("## Matrice de confusion (catégories)\n\n")
        buf.write(_format_confusion(metrics_cat["confusion_matrix"], CATEGORY_CLASSES))
        buf.write("\n\n")
    if metrics_lvl is None:
        buf.write("## Matrice de confusion (niveaux)\n\nNon calculée : aucun cas répondu.\n\n")
    else:
        buf.write("## Matrice de confusion (niveaux)\n\n")
        buf.write(_format_confusion(metrics_lvl["confusion_matrix"], LEVEL_CLASSES))
        buf.write("\n\n")

    buf.write("## Analyse\n\n")
    buf.write(ANALYSIS_TEXT)

    try:
        _atomic_write_text(report_path, buf.getvalue())
    except OSError:
        pass  # Non-fatal; metrics were already printed to stdout.


def _metrics_payload(metrics: dict | None, classes: list[str]) -> dict | None:
    """Convert computed metrics to the JSON report schema."""
    if metrics is None:
        return None
    return {
        "accuracy": metrics["accuracy"],
        "macro_f1": metrics["macro_f1"],
        "per_class": {
            label: {
                "precision": stats["precision"],
                "recall": stats["recall"],
                "f1": stats["f1"],
                "support": stats["support"],
            }
            for label, stats in metrics["per_class"].items()
        },
        "confusion_matrix": {
            "labels": list(classes),
            "rows": [list(row) for row in metrics["confusion_matrix"]],
        },
    }


def _write_report_json(
    metrics_cat: dict | None,
    metrics_lvl: dict | None,
    outcome: EvalOutcome,
    json_path: Path,
    *,
    mocked: bool,
) -> None:
    """Write the structured report consumed by the back-office page."""
    sorted_lat = sorted(outcome.latencies_ms)
    mean_lat = sum(sorted_lat) / len(sorted_lat) if sorted_lat else 0
    p95_lat = sorted_lat[max(0, math.ceil(len(sorted_lat) * 0.95) - 1)] if sorted_lat else 0
    answered_pct = outcome.answered / outcome.total * 100 if outcome.total else 0

    categories = _metrics_payload(metrics_cat, CATEGORY_CLASSES)
    payload = {
        "mode": "mock" if mocked else "real",
        "generated_at": timezone.now().isoformat(),
        "model": get_groq_model(),
        "coverage": {
            "answered": outcome.answered,
            "total": outcome.total,
            "percent": round(answered_pct, 1),
        },
        "errors": {
            "api_total": outcome.api_errors,
            "rate_limit": outcome.rate_limit_errors,
            "timeout": outcome.timeout_errors,
            "other": outcome.other_api_errors,
            "invalid_json_enum": outcome.invalid_model_errors,
        },
        "accuracy": categories["accuracy"] if categories else None,
        "macro_f1": categories["macro_f1"] if categories else None,
        "per_class": categories["per_class"] if categories else None,
        "confusion_matrix": categories["confusion_matrix"] if categories else None,
        "levels": _metrics_payload(metrics_lvl, LEVEL_CLASSES),
        "latency_ms": {
            "mean": round(mean_lat, 1),
            "p95": round(p95_lat, 1),
        },
        "analysis": ANALYSIS_TEXT.replace("**", ""),
    }

    try:
        _atomic_write_text(
            json_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
    except OSError:
        pass  # Non-fatal; the markdown report remains the primary artifact.


def run_evaluation(
    progress_callback: Callable[[int, int], None] | None = None,
    delay: float = DEFAULT_EVAL_DELAY,
    *,
    stdout=None,
    require_answers: bool = False,
) -> EvaluationResult:
    """Run the real Groq evaluation and atomically replace both reports on success."""
    if not get_groq_api_key():
        raise EvaluationNotConfiguredError(
            "GROQ_API_KEY n'est pas configurée. Renseignez la variable d'environnement "
            "avant de lancer une évaluation réelle ; aucun mode simulé n'est utilisé."
        )

    dataset = _load_dataset()
    outcome = _run_eval(
        dataset, stdout, delay=delay, progress_callback=progress_callback
    )
    if require_answers and outcome.answered == 0:
        raise EvaluationError(
            "Aucun cours n'a pu être évalué. Le rapport précédent est conservé."
        )
    if outcome.answered:
        metrics_cat = compute_metrics(outcome.cat_true, outcome.cat_pred, CATEGORY_CLASSES)
        metrics_lvl = compute_metrics(outcome.lvl_true, outcome.lvl_pred, LEVEL_CLASSES)
    else:
        metrics_cat = None
        metrics_lvl = None

    _write_report(metrics_cat, metrics_lvl, outcome, _REPORT_PATH, mocked=False)
    _write_report_json(metrics_cat, metrics_lvl, outcome, _REPORT_JSON_PATH, mocked=False)
    return EvaluationResult(
        outcome=outcome,
        metrics_cat=metrics_cat,
        metrics_lvl=metrics_lvl,
    )


class Command(BaseCommand):
    help = "Show AI assistant metrics. Use --eval to run batch evaluation, --mock for a dry-run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--mock",
            action="store_true",
            help="Generate fake perfect predictions from the dataset (no API calls; used in tests).",
        )
        parser.add_argument(
            "--eval",
            action="store_true",
            help="Call the evaluation entry point on all 36 dataset entries and compute classification metrics.",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=DEFAULT_EVAL_DELAY,
            help=f"Seconds to wait between evaluation entries (default: {DEFAULT_EVAL_DELAY}).",
        )

    def handle(self, *args, **options):
        # --- Section 1: Live operational metrics from MongoDB ---
        try:
            stats = get_ai_stats()
        except PyMongoError as error:
            raise CommandError("Could not read AI metrics.") from error

        self.stdout.write(
            f"Appels: {stats['count']} | moyenne: {stats['average_ms']} ms | "
            f"p95: {stats['p95_ms']} ms | échecs: {stats['failure_rate']} %"
        )

        if stats.get("by_action"):
            self.stdout.write("\nPar action :")
            for action, data in stats["by_action"].items():
                self.stdout.write(
                    f"  {action}: {data['count']} appels | "
                    f"moyenne {data['average_ms']} ms | "
                    f"p95 {data['p95_ms']} ms | "
                    f"échecs {data['failure_rate']} %"
                )

        # --- Section 2: Classification evaluation ---
        run_mock = options.get("mock", False)
        run_eval = options.get("eval", False)

        if not run_mock and not run_eval:
            return

        delay = options.get("delay", DEFAULT_EVAL_DELAY)
        if delay < 0:
            raise CommandError("--delay doit être un nombre positif ou nul.")

        self.stdout.write("\n--- Évaluation de la classification suggest_meta ---\n")
        try:
            dataset = _load_dataset()
        except EvaluationError as error:
            raise CommandError(str(error)) from error
        total = len(dataset)

        if run_mock:
            self.stdout.write(f"Mode --mock : prédictions parfaites simulées sur {total} entrées.\n")
            cat_true, cat_pred, lvl_true, lvl_pred = _build_mock_predictions(dataset)
            outcome = EvalOutcome(
                cat_true=cat_true,
                cat_pred=cat_pred,
                lvl_true=lvl_true,
                lvl_pred=lvl_pred,
                latencies_ms=[50.0] * total,
                answered=total,
                rate_limit_errors=0,
                timeout_errors=0,
                other_api_errors=0,
                invalid_model_errors=0,
                total=total,
            )
            metrics_cat = compute_metrics(
                outcome.cat_true, outcome.cat_pred, CATEGORY_CLASSES
            )
            metrics_lvl = compute_metrics(
                outcome.lvl_true, outcome.lvl_pred, LEVEL_CLASSES
            )
        else:
            try:
                result = run_evaluation(delay=delay, stdout=self.stdout)
            except EvaluationError as error:
                raise CommandError(str(error)) from error
            outcome = result.outcome
            metrics_cat = result.metrics_cat
            metrics_lvl = result.metrics_lvl
            total = outcome.total

        answered_pct = outcome.answered / total * 100 if total else 0
        self.stdout.write(
            f"\nCouverture (répondus) : {outcome.answered}/{total} ({answered_pct:.1f} %)\n"
        )
        self.stdout.write(
            f"Erreurs API : {outcome.api_errors} "
            f"(429 : {outcome.rate_limit_errors}, timeout : {outcome.timeout_errors}, "
            f"autres : {outcome.other_api_errors})\n"
        )
        self.stdout.write(f"Erreurs JSON/enum : {outcome.invalid_model_errors}\n")

        if metrics_cat is None or metrics_lvl is None:
            self.stdout.write("Métriques non calculées : aucun cas répondu.\n")
        else:
            self.stdout.write(
                f"Catégorie — Accuracy: {metrics_cat['accuracy'] * 100:.1f} % | "
                f"Macro-F1: {metrics_cat['macro_f1']:.3f}\n"
            )
            self.stdout.write(
                f"Niveau    — Accuracy: {metrics_lvl['accuracy'] * 100:.1f} % | "
                f"Macro-F1: {metrics_lvl['macro_f1']:.3f}\n"
            )
            self.stdout.write("\nMatrice de confusion (catégories)\n")
            self.stdout.write(_format_confusion(metrics_cat["confusion_matrix"], CATEGORY_CLASSES))
            self.stdout.write("\n")

        if outcome.latencies_ms:
            sorted_lat = sorted(outcome.latencies_ms)
            mean_lat = sum(sorted_lat) / len(sorted_lat)
            p95_lat = sorted_lat[max(0, math.ceil(len(sorted_lat) * 0.95) - 1)]
            self.stdout.write(f"Latence moyenne : {mean_lat:.0f} ms | p95 : {p95_lat:.0f} ms\n")

        if run_mock:
            _write_report(
                metrics_cat,
                metrics_lvl,
                outcome,
                _REPORT_PATH,
                mocked=True,
            )
            _write_report_json(
                metrics_cat,
                metrics_lvl,
                outcome,
                _REPORT_JSON_PATH,
                mocked=True,
            )
        self.stdout.write(f"Rapport écrit dans : {_REPORT_PATH}\n")
        self.stdout.write(f"Rapport JSON écrit dans : {_REPORT_JSON_PATH}\n")
