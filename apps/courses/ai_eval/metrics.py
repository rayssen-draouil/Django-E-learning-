"""
Pure-Python evaluation metrics for AI classification tasks.

No third-party dependencies (no sklearn). Fully unit-testable.
"""
from __future__ import annotations

from typing import Any


def compute_metrics(
    y_true: list[str],
    y_pred: list[str],
    classes: list[str],
) -> dict[str, Any]:
    """Compute accuracy, per-class precision/recall/F1, macro-F1, and confusion matrix.

    Args:
        y_true: Ground-truth class labels.
        y_pred: Predicted class labels (may include values outside ``classes``).
        classes: Ordered list of canonical class names.

    Returns:
        A dict with keys:
            - ``accuracy`` (float 0-1)
            - ``macro_f1`` (float 0-1)
            - ``per_class`` (dict[str, dict]) – keys: precision, recall, f1, support
            - ``confusion_matrix`` (list[list[int]]) – rows=true, cols=pred

    Raises:
        ValueError: If ``y_true`` and ``y_pred`` have different lengths, or are empty.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"y_true and y_pred must have the same length "
            f"(got {len(y_true)} vs {len(y_pred)})"
        )
    if not y_true:
        raise ValueError("y_true and y_pred must not be empty.")

    n = len(classes)
    idx = {cls: i for i, cls in enumerate(classes)}

    # Build confusion matrix (n x n); predictions outside `classes` are ignored for
    # per-class metrics but still count against accuracy.
    matrix: list[list[int]] = [[0] * n for _ in range(n)]

    correct = 0
    for true_label, pred_label in zip(y_true, y_pred):
        if true_label == pred_label:
            correct += 1
        true_i = idx.get(true_label)
        pred_i = idx.get(pred_label)
        if true_i is not None and pred_i is not None:
            matrix[true_i][pred_i] += 1

    accuracy = correct / len(y_true)

    per_class: dict[str, dict[str, float | int]] = {}
    f1_scores: list[float] = []

    for i, cls in enumerate(classes):
        tp = matrix[i][i]
        fp = sum(matrix[r][i] for r in range(n)) - tp   # col sum - tp
        fn = sum(matrix[i][c] for c in range(n)) - tp   # row sum - tp
        support = tp + fn

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        f1_scores.append(f1)
        per_class[cls] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }

    macro_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0

    return {
        "accuracy": round(accuracy, 4),
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion_matrix": matrix,
    }
