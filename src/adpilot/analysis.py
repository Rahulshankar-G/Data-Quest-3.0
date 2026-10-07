import math
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.stats import median_abs_deviation


def robust_anomaly_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    array = np.asarray(values, dtype=float)
    median = float(np.nanmedian(array))
    mad = float(median_abs_deviation(array, nan_policy="omit", scale="normal"))
    if mad < 1e-9:
        mad = max(float(np.nanstd(array)), 1e-9)
    z_scores = np.abs(array - median) / mad
    return np.clip(z_scores / 6 * 100, 0, 100).round(2).tolist()


def decompose_roas(
    current: dict[str, float],
    previous: dict[str, float],
) -> dict[str, Any]:
    keys = ("ctr", "cvr", "aov", "cpc")
    if any(current.get(key, 0) <= 0 or previous.get(key, 0) <= 0 for key in keys):
        return {"identity": "ROAS = CTR × CVR × AOV / CPC", "components": {}, "total_change_pct": None}

    factors = {key: (current[key] / previous[key]) for key in keys}
    signs = {"ctr": 1.0, "cvr": 1.0, "aov": 1.0, "cpc": -1.0}
    shapley = {}
    for key, ratio in factors.items():
        shapley[key] = signs[key] * math.log(ratio)
    log_delta = sum(shapley.values())
    total_pct = (math.exp(log_delta) - 1) * 100
    if abs(log_delta) > 1e-9:
        normalized = {key: round(value / log_delta * total_pct, 2) for key, value in shapley.items()}
    else:
        normalized = {key: 0.0 for key in shapley}
    return {
        "identity": "ROAS = CTR × CVR × AOV / CPC",
        "components": {
            key: {
                "previous": previous[key],
                "current": current[key],
                "rate_change_pct": round((factors[key] - 1) * 100, 2),
                "shapley_contribution_pct": normalized[key],
            }
            for key in keys
        },
        "total_change_pct": round(total_pct, 2),
    }


def oaxaca_shapley_rate_mix(
    previous: list[dict[str, float]],
    current: list[dict[str, float]],
    value_key: str = "conversion_rate",
    mix_key: str = "impressions",
) -> dict[str, float]:
    previous_total = sum(row.get(mix_key, 0) for row in previous)
    current_total = sum(row.get(mix_key, 0) for row in current)
    if previous_total <= 0 or current_total <= 0:
        return {"total_change": 0.0, "rate_effect": 0.0, "mix_effect": 0.0}
    keys = set(row["key"] for row in previous) | set(row["key"] for row in current)
    previous_map = {row["key"]: row for row in previous}
    current_map = {row["key"]: row for row in current}
    rate_effect = 0.0
    mix_effect = 0.0
    for key in keys:
        old = previous_map.get(key, {})
        new = current_map.get(key, {})
        old_mix = old.get(mix_key, 0) / previous_total
        new_mix = new.get(mix_key, 0) / current_total
        old_rate = old.get(value_key, 0)
        new_rate = new.get(value_key, 0)
        rate_effect += (new_rate - old_rate) * (old_mix + new_mix) / 2
        mix_effect += (new_mix - old_mix) * (old_rate + new_rate) / 2
    return {
        "total_change": round(rate_effect + mix_effect, 8),
        "rate_effect": round(rate_effect, 8),
        "mix_effect": round(mix_effect, 8),
    }


def changepoint_indices(values: list[float]) -> list[int]:
    if len(values) < 8:
        return []
    array = np.asarray(values, dtype=float)
    try:
        import ruptures
        return [
            int(index)
            for index in ruptures.Pelt(model="rbf", min_size=3, jump=1)
            .fit(array.reshape(-1, 1))
            .predict(pen=3)
            if index < len(array)
        ]
    except ImportError:
        residual = np.abs(np.diff(array, prepend=array[0]))
        scores = robust_anomaly_scores(residual.tolist())
        return [index for index, score in enumerate(scores) if score >= 85]


def stl_residual_scores(values: list[float], period: int = 7) -> list[float]:
    if len(values) < period * 2:
        return [0.0 for _ in values]
    try:
        from statsmodels.tsa.seasonal import STL
        residuals = STL(np.asarray(values, dtype=float), period=period, robust=True).fit().resid
        return robust_anomaly_scores(np.asarray(residuals).tolist())
    except (ImportError, ValueError, np.linalg.LinAlgError):
        return robust_anomaly_scores(values)


def fit_response_curve(points: list[dict[str, float]]) -> dict[str, float]:
    if len(points) < 3:
        return {"scale": 0.0, "half_saturation": 1.0, "r_squared": 0.0}
    spend = np.asarray([max(0, point["spend"]) for point in points], dtype=float)
    profit = np.asarray([point["incremental_profit"] for point in points], dtype=float)

    def curve(parameters):
        scale, half_saturation = parameters
        return scale * spend / np.maximum(half_saturation + spend, 1e-9)

    fitted = minimize(
        lambda parameters: float(np.square(curve(parameters) - profit).sum()),
        x0=np.array([max(float(profit.max()), 1.0), max(float(np.median(spend)), 1.0)]),
        bounds=[(0.01, max(float(profit.max()) * 10, 1.0)), (0.01, max(float(spend.max()) * 5, 1.0))],
        method="L-BFGS-B",
    )
    residual = float(np.square(curve(fitted.x) - profit).sum())
    total = float(np.square(profit - profit.mean()).sum())
    return {
        "scale": round(float(fitted.x[0]), 4),
        "half_saturation": round(float(fitted.x[1]), 4),
        "r_squared": round(1 - residual / total, 4) if total > 1e-9 else 0.0,
    }


def optimize_budget(
    campaigns: list[dict[str, Any]],
    total_budget: float,
    max_shift_pct: float,
    target_roas: float | None,
) -> dict[str, Any]:
    if not campaigns:
        return {"allocations": [], "predicted_revenue": 0, "predicted_profit": 0}
    spend = np.asarray([max(float(row["spend"]), 0.01) for row in campaigns])
    current_profit = np.asarray([float(row["profit"]) for row in campaigns])
    roas = np.asarray([max(0.0, float(row["roas"])) for row in campaigns])
    inventory_days = np.asarray([float(row.get("inventory_cover_days", 999)) for row in campaigns])
    lower = spend * max(0, 1 - max_shift_pct / 100)
    upper = spend * (1 + max_shift_pct / 100)
    for index, cover in enumerate(inventory_days):
        if cover < 5:
            upper[index] = min(upper[index], spend[index] * 0.55)
            lower[index] = min(lower[index], upper[index])
    if total_budget < float(lower.sum()) or total_budget > float(upper.sum()):
        target_budget = float(np.clip(total_budget, lower.sum(), upper.sum()))
    else:
        target_budget = total_budget
    response = np.maximum(roas * spend - current_profit, 0)

    def predict_profit(allocation):
        ratio = allocation / spend
        hill = allocation / (1 + allocation / np.maximum(spend * 2, 1))
        return current_profit * ratio + response * hill / np.maximum(spend, 1)

    def objective(allocation):
        return -float(predict_profit(allocation).sum())

    constraints = [{"type": "eq", "fun": lambda allocation: float(allocation.sum() - target_budget)}]
    if target_roas is not None and target_roas > 0:
        constraints.append({
            "type": "ineq",
            "fun": lambda allocation: float(
                (predict_profit(allocation).sum() + allocation.sum()) / max(allocation.sum(), 1e-9)
                - target_roas
            ),
        })
    result = minimize(
        objective,
        x0=np.clip(spend, lower, upper),
        bounds=list(zip(lower, upper)),
        constraints=constraints,
        method="SLSQP",
        options={"maxiter": 300, "ftol": 1e-8},
    )
    allocation = result.x if result.success else np.clip(spend, lower, upper)
    predicted = predict_profit(allocation)
    predicted_revenue = predicted + allocation
    return {
        "solver": "scipy.optimize.SLSQP",
        "converged": bool(result.success),
        "status": str(result.message),
        "budget_requested": round(total_budget, 2),
        "budget_optimized": round(float(allocation.sum()), 2),
        "predicted_profit": round(float(predicted.sum()), 2),
        "predicted_revenue": round(float(predicted_revenue.sum()), 2),
        "predicted_roas": round(
            float(predicted_revenue.sum() / max(float(allocation.sum()), 1e-9)),
            4,
        ),
        "allocations": [
            {
                "campaign_id": row["campaign_id"],
                "name": row["name"],
                "current_spend": round(float(spend[index]), 2),
                "optimized_spend": round(float(allocation[index]), 2),
                "change_pct": round(
                    float((allocation[index] / spend[index] - 1) * 100),
                    2,
                ),
                "inventory_cover_days": float(inventory_days[index]),
                "inventory_guardrail_applied": bool(inventory_days[index] < 5),
                "predicted_profit": round(float(predicted[index]), 2),
                "predicted_revenue": round(float(predicted_revenue[index]), 2),
            }
            for index, row in enumerate(campaigns)
        ],
    }
