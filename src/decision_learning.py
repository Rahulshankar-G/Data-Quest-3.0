"""Train a lightweight reviewer-preference model from saved decisions."""

from collections import Counter, defaultdict
from math import exp, isfinite, log
from typing import Any, Callable

MIN_TRAINING_EXAMPLES = 6
MIN_CLASSES = 2
LABELS = ("Approve", "Reject", "Monitor")
MIN_OUTCOME_EXAMPLES = 30
OUTCOME_LABELS = ("Profitable", "Unprofitable")
NUMERIC_FEATURES = (
    "spend",
    "contribution_profit",
    "profit_roas",
    "avg_margin_pct",
    "avg_inventory",
    "roas",
    "cpa",
    "conversion_rate",
    "opportunity_score",
    "confidence",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def extract_features(record: dict[str, Any]) -> list[str]:
    source = record.get("model_features")
    features = source if isinstance(source, dict) else record
    action = features.get("action", record.get("proposed_action", "unknown"))
    platform = features.get("ad_platform", record.get("ad_platform", "unknown"))
    priority = features.get("decision_priority", "unknown")
    tokens = [
        f"action={str(action).strip().lower()}",
        f"platform={str(platform).strip().lower()}",
        f"priority={str(priority).strip().lower()}",
    ]

    numeric_buckets = {
        "spend": (100, 500, 2000),
        "contribution_profit": (-500, 0, 500),
        "profit_roas": (-0.5, 0, 0.5, 1),
        "avg_margin_pct": (0.2, 0.35, 0.5),
        "avg_inventory": (50, 100, 500),
        "roas": (0.5, 1, 2, 4),
        "cpa": (10, 30, 100),
        "conversion_rate": (0.01, 0.03, 0.08),
        "opportunity_score": (0.25, 0.5, 0.75),
        "confidence": (0.6, 0.8, 0.9),
    }
    for field in NUMERIC_FEATURES:
        value = _number(features.get(field))
        if value is None:
            continue
        boundaries = numeric_buckets[field]
        bucket = sum(value >= boundary for boundary in boundaries)
        tokens.append(f"{field}_bucket={bucket}")
    return tokens


def _train_label_model(
    records: list[dict[str, Any]],
    label_getter: Callable[[dict[str, Any]], str | None],
    labels: tuple[str, ...],
    minimum_examples: int,
    model_name: str,
    learning_target: str,
) -> dict[str, Any]:
    examples = [
        (extract_features(record), label)
        for record in records
        if isinstance(record, dict)
        and (label := label_getter(record)) in labels
    ]
    label_counts = Counter(label for _, label in examples)
    status = {
        "ready": False,
        "model": model_name,
        "training_examples": len(examples),
        "minimum_examples": minimum_examples,
        "classes": dict(label_counts),
        "message": (
            f"Collect {max(0, minimum_examples - len(examples))} more "
            f"{learning_target} examples before the model starts predicting."
        ),
    }
    if len(examples) < minimum_examples or len(label_counts) < MIN_CLASSES:
        if len(examples) >= minimum_examples:
            status["message"] = (
                "Collect decisions from at least two different reviewer choices."
                if learning_target == "reviewer decision"
                else f"Collect examples from at least two different {learning_target} classes."
            )
        return status

    vocabulary = {token for tokens, _ in examples for token in tokens}
    class_token_counts: dict[str, Counter[str]] = defaultdict(Counter)
    class_token_totals: Counter[str] = Counter()
    for tokens, label in examples:
        class_token_counts[label].update(tokens)
        class_token_totals[label] += len(tokens)

    status.update({
        "ready": True,
        "message": f"Trained on saved {learning_target} examples.",
        "_label_counts": label_counts,
        "_class_token_counts": class_token_counts,
        "_class_token_totals": class_token_totals,
        "_vocabulary_size": max(1, len(vocabulary)),
    })
    return status


def train_decision_model(records: list[dict[str, Any]]) -> dict[str, Any]:
    return _train_label_model(
        records,
        lambda record: record.get("human_decision"),
        LABELS,
        MIN_TRAINING_EXAMPLES,
        "Multinomial Naive Bayes reviewer-preference model",
        "reviewer decision",
    )


def train_outcome_model(records: list[dict[str, Any]]) -> dict[str, Any]:
    return _train_label_model(
        records,
        lambda record: (
            record.get("outcome", {}).get("profitability_label")
            if isinstance(record.get("outcome"), dict)
            else None
        ),
        OUTCOME_LABELS,
        MIN_OUTCOME_EXAMPLES,
        "Multinomial Naive Bayes measured-profitability model",
        "measured-outcome",
    )


def predict_decision(model: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
    if not model.get("ready"):
        return None

    tokens = extract_features(record)
    label_counts: Counter[str] = model["_label_counts"]
    token_counts: dict[str, Counter[str]] = model["_class_token_counts"]
    token_totals: Counter[str] = model["_class_token_totals"]
    vocabulary_size = model["_vocabulary_size"]
    total_examples = sum(label_counts.values())
    log_scores: dict[str, float] = {}

    for label, class_count in label_counts.items():
        score = log(class_count / total_examples)
        denominator = token_totals[label] + vocabulary_size
        for token in tokens:
            score += log((token_counts[label][token] + 1) / denominator)
        log_scores[label] = score

    peak = max(log_scores.values())
    exp_scores = {label: exp(score - peak) for label, score in log_scores.items()}
    denominator = sum(exp_scores.values())
    probabilities = {
        label: round(score / denominator, 4)
        for label, score in exp_scores.items()
    }
    predicted_decision = max(probabilities, key=probabilities.get)
    return {
        "predicted_decision": predicted_decision,
        "probabilities": probabilities,
        "training_examples": total_examples,
    }
