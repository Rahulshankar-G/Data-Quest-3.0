"""Multivariate detection and next-observation risk modeling for campaign data."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest, RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import balanced_accuracy_score, precision_score, recall_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import OneHotEncoder, RobustScaler
from sklearn.compose import ColumnTransformer

GROUP_COLUMNS = ["ad_platform", "campaign_id"]
NUMERIC_FEATURES = [
    "log_spend",
    "log_revenue",
    "roas",
    "profit_roas",
    "conversion_rate",
    "ctr",
    "avg_margin",
    "avg_stock",
    "avg_discount",
    "avg_competition",
    "roas_change_pct",
    "conversion_change_pct",
]
FORECAST_NUMERIC_FEATURES = [
    "log_spend",
    "log_revenue",
    "roas",
    "profit_roas",
    "conversion_rate",
    "avg_margin",
    "avg_stock",
    "avg_discount",
    "avg_competition",
    "roas_change_pct",
    "conversion_change_pct",
]
FORECAST_CATEGORICAL_FEATURES = ["ad_platform"]
FORECAST_FEATURES = FORECAST_NUMERIC_FEATURES + FORECAST_CATEGORICAL_FEATURES
ALERT_COLUMNS = [
    "day",
    "ad_platform",
    "campaign_id",
    "spend",
    "revenue",
    "contribution_profit",
    "conversions",
    "clicks",
    "roas",
    "profit_roas",
    "roas_change_pct",
    "conversion_rate",
    "conversion_change_pct",
    "avg_margin",
    "avg_stock",
    "avg_competition",
    "avg_discount",
    "anomaly_risk_score",
    "severity",
    "likely_drivers",
]


def build_daily_campaign_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Reconcile source observations into daily campaign/platform signals."""
    if df.empty:
        return pd.DataFrame()

    required = {
        "date",
        "ad_platform",
        "campaign_id",
        "ad_spend",
        "sales_revenue",
        "conversions",
        "estimated_clicks",
        "profit_margin_pct",
        "stock_level",
        "discount_rate",
        "competition_index",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Anomaly analysis requires missing columns: {', '.join(sorted(missing))}")

    source = df.copy().dropna(subset=["date", "ad_platform", "campaign_id"])
    source["day"] = pd.to_datetime(source["date"]).dt.normalize()
    if "total_cogs" not in source:
        source["total_cogs"] = 0.0

    daily = source.groupby(
        ["day", "ad_platform", "campaign_id"],
        as_index=False,
        observed=True,
    ).agg(
        spend=("ad_spend", "sum"),
        revenue=("sales_revenue", "sum"),
        cogs=("total_cogs", "sum"),
        conversions=("conversions", "sum"),
        clicks=("estimated_clicks", "sum"),
        impressions=("impressions", "sum"),
        avg_competition=("competition_index", "mean"),
        avg_discount=("discount_rate", "mean"),
        avg_stock=("stock_level", "mean"),
        avg_margin=("profit_margin_pct", "mean"),
    )
    daily["contribution_profit"] = daily["revenue"] - daily["cogs"] - daily["spend"]
    daily["roas"] = daily["revenue"] / daily["spend"].replace(0, np.nan)
    daily["profit_roas"] = daily["contribution_profit"] / daily["spend"].replace(0, np.nan)
    daily["conversion_rate"] = daily["conversions"] / daily["clicks"].replace(0, np.nan)
    daily["ctr"] = daily["clicks"] / daily["impressions"].replace(0, np.nan)
    daily["log_spend"] = np.log1p(daily["spend"].clip(lower=0))
    daily["log_revenue"] = np.log1p(daily["revenue"].clip(lower=0))

    daily = daily.sort_values(GROUP_COLUMNS + ["day"]).reset_index(drop=True)
    grouped = daily.groupby(GROUP_COLUMNS, observed=True, sort=False)
    daily["previous_roas"] = grouped["roas"].shift(1)
    daily["previous_conversion_rate"] = grouped["conversion_rate"].shift(1)
    daily["roas_change_pct"] = (
        (daily["roas"] - daily["previous_roas"])
        / daily["previous_roas"].abs().replace(0, np.nan)
    )
    daily["conversion_change_pct"] = (
        (daily["conversion_rate"] - daily["previous_conversion_rate"])
        / daily["previous_conversion_rate"].abs().replace(0, np.nan)
    )
    return daily.replace([np.inf, -np.inf], np.nan)


def _add_anomaly_scores(daily: pd.DataFrame) -> pd.DataFrame:
    if daily.empty:
        return daily.assign(
            anomaly_risk_score=pd.Series(dtype=float),
            model_anomaly=pd.Series(dtype=bool),
            is_anomaly=pd.Series(dtype=bool),
            severity=pd.Series(dtype=str),
            likely_drivers=pd.Series(dtype=str),
        )

    model = make_pipeline(
        SimpleImputer(strategy="median", keep_empty_features=True),
        RobustScaler(),
        IsolationForest(
            n_estimators=250,
            contamination=0.04,
            random_state=42,
            n_jobs=-1,
        ),
    )
    matrix = daily[NUMERIC_FEATURES].replace([np.inf, -np.inf], np.nan)
    model.fit(matrix)
    forest = model.named_steps["isolationforest"]
    transformed = model[:-1].transform(matrix)
    anomaly_strength = -forest.score_samples(transformed)
    anomaly_threshold = -forest.offset_
    daily = daily.copy()
    daily["model_anomaly_score"] = anomaly_strength
    daily["model_anomaly"] = anomaly_strength >= anomaly_threshold

    model_rank = pd.Series(anomaly_strength, index=daily.index).rank(pct=True) * 100
    roas_drop = (-daily["roas_change_pct"].fillna(0).clip(upper=0) * 100).clip(0, 100)
    conversion_drop = (
        -daily["conversion_change_pct"].fillna(0).clip(upper=0) * 100
    ).clip(0, 100)
    trend_signal = pd.concat([roas_drop, conversion_drop], axis=1).max(axis=1)
    daily["anomaly_risk_score"] = (0.8 * model_rank + 0.2 * trend_signal).clip(0, 100).round(1)
    daily["is_anomaly"] = daily["model_anomaly"]

    daily["severity"] = "Normal"
    daily.loc[daily["anomaly_risk_score"] >= 80, "severity"] = "Watch"
    daily.loc[daily["anomaly_risk_score"] >= 90, "severity"] = "High"
    daily.loc[daily["anomaly_risk_score"] >= 97, "severity"] = "Critical"
    daily.loc[daily["is_anomaly"] & (daily["severity"] == "Normal"), "severity"] = "High"
    daily.loc[~daily["is_anomaly"] & (daily["severity"] != "Normal"), "severity"] = "Watch"

    daily["likely_drivers"] = daily.apply(_explain_anomaly, axis=1)
    return daily


def _explain_anomaly(row: pd.Series) -> str:
    drivers = []
    if pd.notna(row.get("roas_change_pct")) and row["roas_change_pct"] <= -0.20:
        drivers.append(f"ROAS down {abs(row['roas_change_pct']):.0%} vs previous observation")
    if (
        pd.notna(row.get("conversion_change_pct"))
        and row["conversion_change_pct"] <= -0.20
    ):
        drivers.append(
            f"conversion rate down {abs(row['conversion_change_pct']):.0%}"
        )
    if pd.notna(row.get("avg_stock")) and row["avg_stock"] < 100:
        drivers.append(f"low inventory ({row['avg_stock']:.0f} units)")
    if pd.notna(row.get("avg_margin")) and row["avg_margin"] < 0.20:
        drivers.append(f"low product margin ({row['avg_margin']:.0%})")
    if pd.notna(row.get("avg_discount")) and row["avg_discount"] > 0.30:
        drivers.append(f"high discount ({row['avg_discount']:.0%})")
    if not drivers and bool(row.get("model_anomaly", False)):
        drivers.append("unusual combination of spend, return, margin, inventory, and market signals")
    return "; ".join(drivers) if drivers else "No single contributing signal identified"


def detect_anomalies(
    df: pd.DataFrame,
    warning_threshold: float = -0.20,
    critical_threshold: float = -0.40,
) -> pd.DataFrame:
    """Fit a multivariate outlier model and add transparent trend-based reasons."""
    daily = build_daily_campaign_metrics(df)
    scored = _add_anomaly_scores(daily)
    if scored.empty:
        return scored

    if warning_threshold != -0.20 or critical_threshold != -0.40:
        warning = (
            (scored["roas_change_pct"] < warning_threshold)
            | (scored["conversion_change_pct"] < warning_threshold)
        )
        critical = (
            (scored["roas_change_pct"] < critical_threshold)
            | (scored["conversion_change_pct"] < critical_threshold)
        )
        scored["is_anomaly"] = scored["is_anomaly"] | warning
        scored.loc[warning & (scored["severity"] == "Normal"), "severity"] = "High"
        scored.loc[critical, "severity"] = "Critical"
    return scored


def _forecast_pipeline() -> Any:
    preprocessing = ColumnTransformer(
        transformers=[
            (
                "numeric",
                make_pipeline(
                    SimpleImputer(strategy="median", keep_empty_features=True),
                    RobustScaler(),
                ),
                FORECAST_NUMERIC_FEATURES,
            ),
            (
                "platform",
                OneHotEncoder(handle_unknown="ignore"),
                FORECAST_CATEGORICAL_FEATURES,
            ),
        ],
        remainder="drop",
    )
    classifier = RandomForestClassifier(
        n_estimators=300,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    return make_pipeline(preprocessing, classifier)


def forecast_campaign_anomalies(df: pd.DataFrame) -> dict[str, Any]:
    """Estimate deterioration risk for each campaign's next recorded observation."""
    daily = build_daily_campaign_metrics(df)
    if daily.empty:
        return {
            "model": "Random Forest next-observation deterioration classifier",
            "horizon": "next recorded campaign observation (not a calendar-day forecast)",
            "trained": False,
            "training_examples": 0,
            "message": "No campaign history is available for prediction.",
            "evaluation": None,
            "predictions": [],
        }

    grouped = daily.groupby(GROUP_COLUMNS, observed=True, sort=False)
    daily["next_roas"] = grouped["roas"].shift(-1)
    daily["next_conversion_rate"] = grouped["conversion_rate"].shift(-1)
    next_roas_change = (
        (daily["next_roas"] - daily["roas"]) / daily["roas"].abs().replace(0, np.nan)
    )
    next_conversion_change = (
        (daily["next_conversion_rate"] - daily["conversion_rate"])
        / daily["conversion_rate"].abs().replace(0, np.nan)
    )
    daily["next_period_deterioration"] = (
        (next_roas_change <= -0.30) | (next_conversion_change <= -0.30)
    ).astype(int)
    labeled = daily[daily["next_roas"].notna()].copy()

    minimum_examples = 100
    minimum_class_size = 10
    if len(labeled) < minimum_examples or labeled["next_period_deterioration"].nunique() < 2:
        return {
            "model": "Random Forest next-observation deterioration classifier",
            "horizon": "next recorded campaign observation (not a calendar-day forecast)",
            "trained": False,
            "training_examples": int(len(labeled)),
            "message": "More labeled campaign history is needed to train a reliable early-warning model.",
            "evaluation": None,
            "predictions": [],
        }

    date_cutoff = labeled["day"].quantile(0.8)
    train = labeled[labeled["day"] < date_cutoff]
    test = labeled[labeled["day"] >= date_cutoff]
    if (
        len(train) < minimum_examples // 2
        or train["next_period_deterioration"].nunique() < 2
        or test.empty
        or test["next_period_deterioration"].nunique() < 2
        or min(train["next_period_deterioration"].value_counts()) < minimum_class_size
    ):
        return {
            "model": "Random Forest next-observation deterioration classifier",
            "horizon": "next recorded campaign observation (not a calendar-day forecast)",
            "trained": False,
            "training_examples": int(len(train)),
            "message": "The time-ordered training split does not contain enough examples of each outcome yet.",
            "evaluation": None,
            "predictions": [],
        }

    evaluation_model = _forecast_pipeline()
    evaluation_model.fit(train[FORECAST_FEATURES], train["next_period_deterioration"])
    test_actual = test["next_period_deterioration"]
    test_predicted = evaluation_model.predict(test[FORECAST_FEATURES])
    evaluation = {
        "test_examples": int(len(test)),
        "balanced_accuracy": round(
            float(balanced_accuracy_score(test_actual, test_predicted)), 3
        ),
        "precision": round(float(precision_score(test_actual, test_predicted, zero_division=0)), 3),
        "recall": round(float(recall_score(test_actual, test_predicted, zero_division=0)), 3),
        "time_split": str(date_cutoff.date()),
    }

    final_model = _forecast_pipeline()
    final_model.fit(labeled[FORECAST_FEATURES], labeled["next_period_deterioration"])
    latest = (
        daily.sort_values("day")
        .groupby(GROUP_COLUMNS, observed=True, sort=False)
        .tail(1)
        .copy()
    )
    latest["deterioration_risk"] = final_model.predict_proba(
        latest[FORECAST_FEATURES]
    )[:, 1]
    latest["risk_score"] = (latest["deterioration_risk"] * 100).round(1)
    latest["risk_level"] = pd.cut(
        latest["risk_score"],
        bins=[-1, 39.9, 64.9, 84.9, 100],
        labels=["Low", "Moderate", "High", "Critical"],
    ).astype(str)
    latest["likely_drivers"] = latest.apply(_explain_anomaly, axis=1)

    predictions = []
    for _, row in latest.sort_values("risk_score", ascending=False).iterrows():
        predictions.append({
            "day": row["day"].date().isoformat(),
            "ad_platform": row["ad_platform"],
            "campaign_id": row["campaign_id"],
            "spend": _json_number(row["spend"]),
            "revenue": _json_number(row["revenue"]),
            "contribution_profit": _json_number(row["contribution_profit"]),
            "roas": _json_number(row["roas"]),
            "profit_roas": _json_number(row["profit_roas"]),
            "conversion_rate": _json_number(row["conversion_rate"]),
            "avg_margin": _json_number(row["avg_margin"]),
            "avg_stock": _json_number(row["avg_stock"]),
            "risk_score": float(row["risk_score"]),
            "risk_level": row["risk_level"],
            "likely_drivers": row["likely_drivers"],
        })

    return {
        "model": "Random Forest next-observation deterioration classifier",
        "horizon": "next recorded campaign observation (not a calendar-day forecast)",
        "target": "At least a 30% drop in ROAS or conversion rate at the next recorded observation",
        "trained": True,
        "training_examples": int(len(labeled)),
        "positive_examples": int(labeled["next_period_deterioration"].sum()),
        "evaluation": evaluation,
        "message": (
            "Risk scores estimate deterioration at the next recorded campaign observation. "
            "They are not calibrated probabilities or guaranteed outcomes."
        ),
        "predictions": predictions,
    }


def _json_number(value: Any) -> float | None:
    if pd.isna(value) or not np.isfinite(value):
        return None
    return round(float(value), 4)


def build_ai_alert_payload(anomaly_row: pd.Series) -> dict[str, Any]:
    """Convert a scored alert row into JSON-safe evidence."""
    payload = {}
    for column in ALERT_COLUMNS:
        value = anomaly_row.get(column)
        if isinstance(value, (list, tuple)):
            payload[column] = list(value)
        elif pd.isna(value):
            payload[column] = None
        elif hasattr(value, "isoformat"):
            payload[column] = value.isoformat()
        elif hasattr(value, "item"):
            payload[column] = value.item()
        else:
            payload[column] = value
    return payload


if __name__ == "__main__":
    from src.metrics import load_data

    source_data = load_data()
    results = detect_anomalies(source_data)
    forecast = forecast_campaign_anomalies(source_data)
    print(f"Daily campaign observations: {len(results)}")
    print(f"Model-identified anomalies: {int(results['is_anomaly'].sum())}")
    print(f"Forecast predictions: {len(forecast['predictions'])}")
    print(f"Forecast evaluation: {forecast['evaluation']}")
