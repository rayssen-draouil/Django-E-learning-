"""Load and shape the JSON report written by ``manage.py ai_report``."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

REPORT_JSON_PATH = Path(__file__).resolve().parent / "report.json"


class AIReportError(Exception):
    """Base class for AI report loading errors."""


class AIReportNotFoundError(AIReportError):
    """Raised when no report has been generated yet."""


class AIReportInvalidError(AIReportError):
    """Raised when the report file is unreadable or does not match the schema."""


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_metrics(metrics: Any) -> bool:
    if metrics is None:
        return True
    if not isinstance(metrics, dict):
        return False
    if not _is_number(metrics.get("accuracy")) or not _is_number(metrics.get("macro_f1")):
        return False

    per_class = metrics.get("per_class")
    if not isinstance(per_class, dict) or not per_class:
        return False
    for label, stats in per_class.items():
        if not isinstance(label, str) or not isinstance(stats, dict):
            return False
        for key in ("precision", "recall", "f1"):
            if not _is_number(stats.get(key)):
                return False
        if not _is_count(stats.get("support")):
            return False

    matrix = metrics.get("confusion_matrix")
    if not isinstance(matrix, dict):
        return False
    labels = matrix.get("labels")
    rows = matrix.get("rows")
    if not isinstance(labels, list) or not labels:
        return False
    if not all(isinstance(label, str) for label in labels):
        return False
    if not isinstance(rows, list) or len(rows) != len(labels):
        return False
    for row in rows:
        if not isinstance(row, list) or len(row) != len(labels):
            return False
        if not all(_is_count(value) for value in row):
            return False
    return True


def _validate(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise AIReportInvalidError("Le rapport IA n'est pas un objet JSON.")
    if data.get("mode") not in {"mock", "real"}:
        raise AIReportInvalidError("Le mode du rapport IA est invalide.")
    if not isinstance(data.get("generated_at"), str) or not data["generated_at"]:
        raise AIReportInvalidError("La date de génération du rapport IA est invalide.")
    if not isinstance(data.get("model"), str) or not data["model"]:
        raise AIReportInvalidError("Le modèle du rapport IA est invalide.")

    coverage = data.get("coverage")
    if not isinstance(coverage, dict):
        raise AIReportInvalidError("La couverture du rapport IA est invalide.")
    if not _is_count(coverage.get("answered")) or not _is_count(coverage.get("total")):
        raise AIReportInvalidError("La couverture du rapport IA est invalide.")
    if coverage["answered"] > coverage["total"] or not _is_number(coverage.get("percent")):
        raise AIReportInvalidError("La couverture du rapport IA est invalide.")

    errors = data.get("errors")
    if not isinstance(errors, dict):
        raise AIReportInvalidError("Les erreurs du rapport IA sont invalides.")
    for key in ("api_total", "rate_limit", "timeout", "other", "invalid_json_enum"):
        if not _is_count(errors.get(key)):
            raise AIReportInvalidError("Les erreurs du rapport IA sont invalides.")

    latency = data.get("latency_ms")
    if not isinstance(latency, dict):
        raise AIReportInvalidError("Les latences du rapport IA sont invalides.")
    if not _is_number(latency.get("mean")) or not _is_number(latency.get("p95")):
        raise AIReportInvalidError("Les latences du rapport IA sont invalides.")

    top_level_metrics = {
        "accuracy": data.get("accuracy"),
        "macro_f1": data.get("macro_f1"),
        "per_class": data.get("per_class"),
        "confusion_matrix": data.get("confusion_matrix"),
    }
    if any(value is not None for value in top_level_metrics.values()):
        if not _valid_metrics(top_level_metrics):
            raise AIReportInvalidError("Les métriques de catégorie du rapport IA sont invalides.")
    if not _valid_metrics(data.get("levels")):
        raise AIReportInvalidError("Les métriques de niveau du rapport IA sont invalides.")

    if not isinstance(data.get("analysis"), str):
        raise AIReportInvalidError("L'analyse du rapport IA est invalide.")
    return data


def load_report(path: Path | None = None) -> dict[str, Any]:
    """Read and validate ``report.json``; raise a typed error when unavailable."""
    target = Path(path) if path is not None else REPORT_JSON_PATH
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise AIReportNotFoundError("Aucun rapport IA n'a encore été généré.") from error
    except (OSError, UnicodeDecodeError) as error:
        raise AIReportInvalidError("Le rapport IA est illisible.") from error

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise AIReportInvalidError("Le rapport IA est un JSON invalide.") from error
    return _validate(data)


def _format_percent(ratio: float) -> str:
    return f"{ratio * 100:.1f} %".replace(".", ",")


def _format_percent_points(percent: float) -> str:
    return f"{percent:.1f} %".replace(".", ",")


def _format_score(value: float) -> str:
    return f"{value:.3f}".replace(".", ",")


def _format_ms(value: float) -> str:
    return f"{value:.0f} ms"


def _metrics_view(metrics: dict[str, Any] | None) -> dict[str, Any] | None:
    if metrics is None:
        return None
    per_class = [
        {
            "label": label,
            "precision": _format_score(stats["precision"]),
            "recall": _format_score(stats["recall"]),
            "f1": _format_score(stats["f1"]),
            "support": stats["support"],
        }
        for label, stats in metrics["per_class"].items()
    ]
    labels = metrics["confusion_matrix"]["labels"]
    matrix_rows = [
        {
            "label": label,
            "cells": [
                {"value": value, "diagonal": column == row_index}
                for column, value in enumerate(row)
            ],
        }
        for row_index, (label, row) in enumerate(zip(labels, metrics["confusion_matrix"]["rows"]))
    ]
    return {
        "accuracy": _format_percent(metrics["accuracy"]),
        "macro_f1": _format_score(metrics["macro_f1"]),
        "per_class": per_class,
        "labels": labels,
        "matrix_rows": matrix_rows,
    }


def build_report_view(report: dict[str, Any]) -> dict[str, Any]:
    """Prepare display-ready values for the template."""
    coverage = report["coverage"]
    errors = report["errors"]
    latency = report["latency_ms"]
    category_metrics = None
    if report.get("accuracy") is not None:
        category_metrics = {
            "accuracy": report.get("accuracy"),
            "macro_f1": report.get("macro_f1"),
            "per_class": report.get("per_class"),
            "confusion_matrix": report.get("confusion_matrix"),
        }
    categories = _metrics_view(category_metrics)
    try:
        generated_at_display = datetime.fromisoformat(report["generated_at"]).strftime(
            "%d/%m/%Y à %H:%M"
        )
    except ValueError:
        generated_at_display = report["generated_at"]
    return {
        "mode": report["mode"],
        "is_mock": report["mode"] == "mock",
        "generated_at": report["generated_at"],
        "generated_at_display": generated_at_display,
        "model": report["model"],
        "coverage_display": f"{coverage['answered']}/{coverage['total']}",
        "coverage_percent": _format_percent_points(coverage["percent"]),
        "error_total": errors["api_total"] + errors["invalid_json_enum"],
        "api_errors": errors["api_total"],
        "rate_limit_errors": errors["rate_limit"],
        "timeout_errors": errors["timeout"],
        "other_api_errors": errors["other"],
        "invalid_model_errors": errors["invalid_json_enum"],
        "mean_latency": _format_ms(latency["mean"]),
        "p95_latency": _format_ms(latency["p95"]),
        "accuracy": categories["accuracy"] if categories else None,
        "macro_f1": categories["macro_f1"] if categories else None,
        "categories": categories,
        "levels": _metrics_view(report.get("levels")),
        "analysis": report["analysis"],
    }
