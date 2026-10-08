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
    value = np.asarray([max(0, point["value"]) for point in points], dtype=float)

    def curve(parameters):
        scale, half_saturation = parameters
        return scale * spend / np.maximum(half_saturation + spend, 1e-9)

    fitted = minimize(
        lambda parameters: float(np.square(curve(parameters) - value).sum()),
        x0=np.array([max(float(value.max()), 1.0), max(float(np.median(spend)), 1.0)]),
        bounds=[(0.01, max(float(value.max()) * 10, 1.0)), (0.01, max(float(spend.max()) * 5, 1.0))],
        method="L-BFGS-B",
    )
    if fitted.success and np.isfinite(fitted.x).all():
        parameters = fitted.x
    else:
        scale_limit = max(float(value.max()) * 10, 1.0)
        half_saturation_limit = max(float(spend.max()) * 5, 1.0)
        best_error = float("inf")
        parameters = np.array([max(float(value.max()), 0.01), max(float(np.median(spend)), 0.01)])
        for half_saturation in np.geomspace(0.01, half_saturation_limit, 512):
            response = spend / np.maximum(half_saturation + spend, 1e-9)
            scale = float(np.clip(np.dot(response, value) / np.dot(response, response), 0.01, scale_limit))
            error = float(np.square(scale * response - value).sum())
            if error < best_error:
                best_error = error
                parameters = np.array([scale, half_saturation])
    residual = float(np.square(curve(parameters) - value).sum())
    total = float(np.square(value - value.mean()).sum())
    return {
        "scale": round(float(parameters[0]), 4),
        "half_saturation": round(float(parameters[1]), 4),
        "r_squared": round(1 - residual / total, 4) if total > 1e-9 else 0.0,
    }


def optimize_budget(
    campaigns: list[dict[str, Any]],
    total_budget: float,
    max_shift_pct: float,
    target_roas: float | None,
) -> dict[str, Any]:
    if not campaigns:
        return {
            "solver": "scipy.optimize.SLSQP",
            "converged": False,
            "status": "No campaigns have usable performance data.",
            "budget_requested": round(total_budget, 2),
            "budget_optimized": 0,
            "budget_adjusted": total_budget != 0,
            "target_roas_met": target_roas is None or target_roas <= 0,
            "predicted_revenue": 0,
            "predicted_profit": 0,
            "predicted_roas": 0,
            "allocations": [],
        }
    spend = np.asarray([
        max(float(row.get("daily_budget", row.get("spend", 0.01))), 0.01)
        for row in campaigns
    ])
    roas = np.asarray([max(0.0, float(row.get("roas", 0))) for row in campaigns])
    revenue_curves = [
        row.get("revenue_curve", {
            "scale": roas[index] * spend[index] * 2,
            "half_saturation": spend[index],
        })
        for index, row in enumerate(campaigns)
    ]
    cogs_rates = np.asarray([
        max(0.0, float(row.get("cogs_rate", 0)))
        for row in campaigns
    ])
    inventory_days = np.asarray([float(row.get("inventory_cover_days", 999)) for row in campaigns])
    lower = spend * max(0, 1 - max_shift_pct / 100)
    upper = spend * (1 + max_shift_pct / 100)
    for index, cover in enumerate(inventory_days):
        if cover < 5:
            upper[index] = min(upper[index], spend[index] * 0.55)
            lower[index] = min(lower[index], upper[index])
    target_budget = float(np.clip(total_budget, lower.sum(), upper.sum()))
    budget_adjusted = not math.isclose(target_budget, total_budget, abs_tol=0.01)

    def predict_revenue(allocation):
        return np.asarray([
            float(curve["scale"]) * allocation[index]
            / max(float(curve["half_saturation"]) + allocation[index], 1e-9)
            for index, curve in enumerate(revenue_curves)
        ])

    def predict_profit(allocation):
        return predict_revenue(allocation) * (1 - cogs_rates) - allocation

    def feasible_start():
        allocation = np.clip(spend, lower, upper)
        difference = target_budget - float(allocation.sum())
        for _ in range(len(allocation) + 1):
            if abs(difference) <= 1e-7:
                break
            capacity = upper - allocation if difference > 0 else allocation - lower
            available = capacity > 1e-9
            if not np.any(available):
                break
            shares = capacity[available] / float(capacity[available].sum())
            adjustment = np.minimum(capacity[available], abs(difference) * shares)
            allocation[available] += adjustment if difference > 0 else -adjustment
            difference = target_budget - float(allocation.sum())
        return allocation

    initial_allocation = feasible_start()
    constraints = [{"type": "eq", "fun": lambda allocation: float(allocation.sum() - target_budget)}]
    if target_roas is not None and target_roas > 0:
        constraints.append({
            "type": "ineq",
            "fun": lambda allocation: float(
                float(predict_revenue(allocation).sum()) / max(float(allocation.sum()), 1e-9)
                - target_roas
            ),
        })
    result = minimize(
        lambda allocation: -float(predict_profit(allocation).sum()),
        x0=initial_allocation,
        bounds=list(zip(lower, upper)),
        constraints=constraints,
        method="SLSQP",
        options={"maxiter": 300, "ftol": 1e-8},
    )
    allocation = result.x if result.success else initial_allocation
    predicted = predict_profit(allocation)
    revenue = predict_revenue(allocation)
    achieved_roas = float(revenue.sum() / max(float(allocation.sum()), 1e-9))
    return {
        "solver": "scipy.optimize.SLSQP",
        "converged": bool(result.success),
        "status": str(result.message),
        "budget_requested": round(total_budget, 2),
        "budget_optimized": round(float(allocation.sum()), 2),
        "budget_adjusted": budget_adjusted,
        "target_roas_met": target_roas is None or target_roas <= 0 or achieved_roas + 1e-6 >= target_roas,
        "predicted_profit": round(float(predicted.sum()), 2),
        "predicted_revenue": round(float(revenue.sum()), 2),
        "predicted_roas": round(achieved_roas, 4),
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
                "predicted_revenue": round(float(revenue[index]), 2),
            }
            for index, row in enumerate(campaigns)
        ],
    }
