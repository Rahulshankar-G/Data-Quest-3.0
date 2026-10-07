import json
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

try:
    from src.metrics import campaign_summary, load_data
except ModuleNotFoundError:
    from metrics import campaign_summary, load_data


# ---------------------------------------------------------------------------
# Business Constraints & Threshold Constants
# ---------------------------------------------------------------------------
LOW_STOCK_THRESHOLD = 100       # Safety inventory buffer (units)
MIN_MARGIN = 0.35              # Minimum profit margin required to scale (35%)
INCREASE_PCT = 20              # Budget scale step (%)
REDUCE_PCT = -20               # Budget trim step (%)

# Business Decision Priorities (strictly ranked in operational order)
PRIORITY_ORDER = [
    "Protect inventory",       # 1. Highest urgency: prevent stockout disasters
    "Scale profitable growth", # 2. Capitalize on high-efficiency profit drivers
    "Stop waste",              # 3. Cut unprofitable ad spend bleeding cash
    "Monitor",                 # 4. Healthy/baseline campaigns under observation
]


# ---------------------------------------------------------------------------
# Scoring Utilities
# ---------------------------------------------------------------------------
def min_max_score(series: pd.Series, clip_bounds: bool = True) -> pd.Series:
    clean = pd.to_numeric(series, errors="coerce").fillna(0.0).replace([np.inf, -np.inf], 0.0)

    minimum = clean.min()
    maximum = clean.max()

    if maximum == minimum or pd.isna(minimum) or pd.isna(maximum):
        return pd.Series(0.5, index=series.index)

    normalized = (clean - minimum) / (maximum - minimum)
    if clip_bounds:
        normalized = normalized.clip(0.0, 1.0)

    return normalized.fillna(0.5)


def _compute_dynamic_confidence(
    action: str,
    conversions: float,
    spend: float,
    avg_inventory: float,
    contribution_profit: float,
    avg_margin_pct: float,
) -> float:

    conversions = float(conversions or 0.0)
    spend = float(spend or 0.0)
    avg_inventory = float(avg_inventory or 0.0)
    contribution_profit = float(contribution_profit or 0.0)
    avg_margin_pct = float(avg_margin_pct or 0.0)

    # Sample size factor: more conversions = higher statistical reliability
    volume_factor = float(np.clip(np.log1p(max(conversions, 0.0)) / np.log1p(150.0), 0.0, 1.0)) * 0.06

    if action == "Hold budget":
        # Base anchor: 0.90. Closer to 0 stock = absolute certainty of stockout
        depth = float(np.clip((LOW_STOCK_THRESHOLD - avg_inventory) / LOW_STOCK_THRESHOLD, 0.0, 1.0)) * 0.06
        conf = 0.88 + depth + volume_factor * 0.5
        return round(float(np.clip(conf, 0.75, 0.98)), 2)

    elif action == "Reduce budget":
        # Base anchor: 0.88. Larger dollar losses = higher certainty of waste
        loss_scale = float(np.clip(abs(contribution_profit) / 1500.0, 0.0, 1.0)) * 0.05
        conf = 0.84 + loss_scale + volume_factor
        return round(float(np.clip(conf, 0.70, 0.96)), 2)

    elif action == "Increase budget":
        # Base anchor: 0.82. Larger margin cushion above 35% = higher certainty
        margin_cushion = float(np.clip((avg_margin_pct - MIN_MARGIN) / 0.15, 0.0, 1.0)) * 0.06
        conf = 0.78 + margin_cushion + volume_factor
        return round(float(np.clip(conf, 0.70, 0.95)), 2)

    else:  # Maintain budget / Monitor
        # Base anchor: 0.65. Observational state
        conf = 0.60 + volume_factor * 0.8
        return round(float(np.clip(conf, 0.55, 0.78)), 2)


def _generate_dynamic_reason(
    action: str,
    contribution_profit: float,
    spend: float,
    profit_roas: float,
    avg_margin_pct: float,
    avg_inventory: float,
    budget_change_pct: int,
) -> str:
    contribution_profit = float(contribution_profit or 0.0)
    spend = float(spend or 0.0)
    profit_roas = float(profit_roas or 0.0)
    avg_margin_pct = float(avg_margin_pct or 0.0)
    avg_inventory = float(avg_inventory or 0.0)
    budget_change_pct = int(budget_change_pct or 0)

    if action == "Hold budget":
        return (
            f"Inventory safety alert: Stock level is {avg_inventory:.0f} units "
            f"(below safety threshold of {LOW_STOCK_THRESHOLD}). "
            f"Holding ad budget to prevent stockout and fulfillment disruption."
        )

    elif action == "Reduce budget":
        return (
            f"Unprofitable ad spend: Generated -${abs(contribution_profit):,.2f} contribution profit "
            f"on ${spend:,.2f} spend (Profit ROAS {profit_roas:.2f}x). "
            f"Trim budget by {abs(budget_change_pct)}% to stop capital waste."
        )

    elif action == "Increase budget":
        return (
            f"Profitable growth driver: Generated +${contribution_profit:,.2f} contribution profit "
            f"({profit_roas:.2f}x Profit ROAS) with {avg_margin_pct * 100:.1f}% margin (>= 35%) "
            f"and healthy inventory ({avg_inventory:.0f} units). Scale budget by +{budget_change_pct}%."
        )

    else:  # Maintain budget
        if contribution_profit > 0 and avg_margin_pct < MIN_MARGIN:
            return (
                f"Profitable (+${contribution_profit:,.2f}) but margin of "
                f"{avg_margin_pct * 100:.1f}% is below {MIN_MARGIN * 100:.0f}% scale threshold. "
                f"Maintain budget and focus on margin optimization."
            )
        elif contribution_profit >= 0:
            return (
                f"Break-even performance (Profit: +${contribution_profit:,.2f}, Profit ROAS: {profit_roas:.2f}x). "
                f"Maintain current budget and monitor conversion trends."
            )
        else:
            return "Performance is within normal decision boundaries. Maintain budget."


# ---------------------------------------------------------------------------
# Core Decision Engine
# ---------------------------------------------------------------------------
def generate_recommendations(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        results = summary.copy()
        for col in [
            "profit_roas", "profit_score", "margin_score", "inventory_score",
            "conversion_score", "opportunity_score", "action", "budget_change_pct",
            "decision_priority", "reason", "confidence"
        ]:
            if col not in results.columns:
                results[col] = pd.Series(dtype=object)
        return results

    results = summary.copy()

    # Ensure required input columns exist with safe defaults
    for col in ["spend", "contribution_profit", "avg_margin_pct", "avg_inventory", "conversion_rate", "conversions"]:
        if col not in results.columns:
            results[col] = 0.0

    # 1. Compute Profit ROAS (Profit per dollar of ad spend)
    results["profit_roas"] = (
        pd.to_numeric(results["contribution_profit"], errors="coerce")
        / pd.to_numeric(results["spend"], errors="coerce").replace(0, pd.NA)
    ).fillna(0.0).astype(float)

    # 2. Individual dimension scores (normalized 0.0 - 1.0)
    results["profit_score"] = min_max_score(results["profit_roas"])
    results["margin_score"] = min_max_score(results["avg_margin_pct"])
    results["inventory_score"] = min_max_score(results["avg_inventory"])
    results["conversion_score"] = min_max_score(results["conversion_rate"])

    # 3. Overall composite Opportunity Score (Task Guide Weights: 40% / 25% / 20% / 15%)
    results["opportunity_score"] = (
        0.40 * results["profit_score"]
        + 0.25 * results["margin_score"]
        + 0.20 * results["inventory_score"]
        + 0.15 * results["conversion_score"]
    ).round(4)

    # 4. Initialize default baseline state
    results["action"] = "Maintain budget"
    results["budget_change_pct"] = 0
    results["decision_priority"] = "Monitor"

    # 5. Define mutually-exclusive business rule conditions
    low_stock = results["avg_inventory"] < LOW_STOCK_THRESHOLD
    negative_profit = results["contribution_profit"] < 0
    scalable = (
        (results["contribution_profit"] > 0)
        & (results["profit_roas"] > 0)
        & (results["avg_margin_pct"] >= MIN_MARGIN)
        & ~low_stock
    )

    # -----------------------------------------------------------------------
    # RULE 1. HOLD BUDGET (Inventory protection overrides all scale recommendations)
    # -----------------------------------------------------------------------
    results.loc[low_stock, "action"] = "Hold budget"
    results.loc[low_stock, "budget_change_pct"] = 0
    results.loc[low_stock, "decision_priority"] = "Protect inventory"

    # -----------------------------------------------------------------------
    # RULE 2. REDUCE BUDGET (Negative profit + sufficient inventory to prevent bleed)
    # -----------------------------------------------------------------------
    results.loc[negative_profit & ~low_stock, "action"] = "Reduce budget"
    results.loc[negative_profit & ~low_stock, "budget_change_pct"] = REDUCE_PCT
    results.loc[negative_profit & ~low_stock, "decision_priority"] = "Stop waste"

    # -----------------------------------------------------------------------
    # RULE 3. INCREASE BUDGET (Profitable, adequate margin >= 35%, and healthy stock)
    # -----------------------------------------------------------------------
    results.loc[scalable, "action"] = "Increase budget"
    results.loc[scalable, "budget_change_pct"] = INCREASE_PCT
    results.loc[scalable, "decision_priority"] = "Scale profitable growth"

    # 6. Generate Data-Backed Diagnostic Reasons
    reasons = [
        _generate_dynamic_reason(
            action=str(row["action"]),
            contribution_profit=float(row.get("contribution_profit") or 0.0),
            spend=float(row.get("spend") or 0.0),
            profit_roas=float(row.get("profit_roas") or 0.0),
            avg_margin_pct=float(row.get("avg_margin_pct") or 0.0),
            avg_inventory=float(row.get("avg_inventory") or 0.0),
            budget_change_pct=int(row.get("budget_change_pct") or 0),
        )
        for _, row in results.iterrows()
    ]
    results["reason"] = reasons

    # 7. Compute Statistically Grounded Dynamic Confidence
    confidences = [
        _compute_dynamic_confidence(
            action=str(row["action"]),
            conversions=float(row.get("conversions") or 0.0),
            spend=float(row.get("spend") or 0.0),
            avg_inventory=float(row.get("avg_inventory") or 0.0),
            contribution_profit=float(row.get("contribution_profit") or 0.0),
            avg_margin_pct=float(row.get("avg_margin_pct") or 0.0),
        )
        for _, row in results.iterrows()
    ]
    results["confidence"] = confidences

    # 8. Sort by True Operational Business Priority, then Opportunity Score
    results["decision_priority"] = pd.Categorical(
        results["decision_priority"],
        categories=PRIORITY_ORDER,
        ordered=True,
    )

    results = results.sort_values(
        ["decision_priority", "opportunity_score"],
        ascending=[True, False],
    )

    # Cast back to clean standard string for full downstream compatibility
    results["decision_priority"] = results["decision_priority"].astype(str)

    return results


# ---------------------------------------------------------------------------
# Payload Generator for AI Explanation & Dashboard Layers
# ---------------------------------------------------------------------------
def build_recommendation_payload(row: Any) -> Dict[str, Any]:
    if hasattr(row, "to_dict"):
        data = row.to_dict()
    elif isinstance(row, dict):
        data = row
    else:
        data = dict(row)

    # 1. Base required schema fields from Person 4 Task Guide
    base_fields = [
        "ad_platform",
        "campaign_id",
        "sku_id",
        "spend",
        "revenue",
        "contribution_profit",
        "profit_roas",
        "avg_margin_pct",
        "avg_inventory",
        "roas",
        "cpa",
        "conversion_rate",
        "opportunity_score",
        "action",
        "budget_change_pct",
        "reason",
        "decision_priority",
        "confidence",
    ]

    payload: Dict[str, Any] = {}
    for field in base_fields:
        val = data.get(field)
        if pd.isna(val):
            val = None
        elif hasattr(val, "item"):
            val = val.item()
        elif isinstance(val, (np.floating, float)):
            val = round(float(val), 4)
        elif isinstance(val, (np.integer, int)):
            val = int(val)
        payload[field] = val

    # 2. Enrich with structured reasoning for the AI model prompt
    spend = float(payload.get("spend") or 0.0)
    change_pct = float(payload.get("budget_change_pct") or 0.0)
    action = payload.get("action") or "Maintain budget"
    platform = payload.get("ad_platform") or "Ad Platform"
    sku = payload.get("sku_id") or "SKU"
    profit = float(payload.get("contribution_profit") or 0.0)
    p_roas = float(payload.get("profit_roas") or 0.0)
    margin = float(payload.get("avg_margin_pct") or 0.0)
    inv = float(payload.get("avg_inventory") or 0.0)
    roas_val = float(payload.get("roas") or 0.0)
    cpa_val = float(payload.get("cpa") or 0.0)

    # Supporting Evidence bullets
    payload["supporting_evidence"] = [
        f"Contribution Profit: ${profit:+,.2f}",
        f"Profit ROAS: {p_roas:.2f}x net return on ad spend",
        f"Gross Margin: {margin * 100:.1f}% (Safety Threshold: {MIN_MARGIN * 100:.0f}%)",
        f"Average Inventory: {inv:.0f} units (Safety Threshold: {LOW_STOCK_THRESHOLD} units)",
        f"Ad Efficiency: {roas_val:.2f}x Revenue ROAS, CPA ${cpa_val:.2f}",
    ]

    # Specific Operational Risk for LLM explanation
    if action == "Hold budget":
        payload["key_risk"] = (
            f"Stockout risk: Inventory ({inv:.0f} units) is below safety buffer. "
            f"Scaling or continuing aggressive spend will lead to unfulfilled orders."
        )
        payload["operational_next_step"] = (
            f"Throttle or pause ad sets for {sku} on {platform}; coordinate with supply chain "
            f"to expedite replenishment before resuming campaign scale."
        )
    elif action == "Reduce budget":
        payload["key_risk"] = (
            f"Capital drain risk: Campaign is losing -${abs(profit):,.2f}. Continued spend "
            f"at current CPA degrades overall portfolio profitability."
        )
        payload["operational_next_step"] = (
            f"Apply a {abs(int(change_pct))}% budget cut on {platform}; review audience targeting "
            f"and pause lowest-performing ad creatives."
        )
    elif action == "Increase budget":
        payload["key_risk"] = (
            f"Inventory depletion & ad fatigue: Rapid spend growth (+{int(change_pct)}%) could "
            f"deplete current stock ({inv:.0f} units) or increase marginal CPA."
        )
        payload["operational_next_step"] = (
            f"Increase daily budget by {int(change_pct)}% in {platform} Ads Manager; "
            f"set automated alert if CPA increases by more than 15%."
        )
    else:
        payload["key_risk"] = (
            "Opportunity cost risk: Maintaining spend while auction costs fluctuate."
        )
        payload["operational_next_step"] = (
            f"Maintain daily budget on {platform}; test new creative angles to improve conversion rate."
        )

    # Projected Financial Impact
    projected_spend_delta = round(spend * (change_pct / 100.0), 2)
    projected_new_spend = round(spend + projected_spend_delta, 2)
    payload["projected_spend_delta"] = projected_spend_delta
    payload["projected_new_spend"] = projected_new_spend

    return payload


# ---------------------------------------------------------------------------
# Portfolio Roll-up Summary (Bonus for Streamlit Dashboard & Executive Team)
# ---------------------------------------------------------------------------
def portfolio_summary(recommendations: pd.DataFrame) -> Dict[str, Any]:
    if recommendations.empty:
        return {
            "total_campaigns_evaluated": 0,
            "action_breakdown": {},
            "total_ad_spend": 0.0,
            "total_contribution_profit": 0.0,
            "net_budget_reallocation": 0.0,
            "ad_waste_eliminated": 0.0,
            "growth_capital_deployed": 0.0,
            "inventory_alerts_count": 0,
        }

    total_campaigns = len(recommendations)
    action_counts = recommendations["action"].value_counts().to_dict()

    total_spend = float(recommendations["spend"].sum())
    total_profit = float(recommendations["contribution_profit"].sum())

    # Calculate projected spend shifts
    spend_deltas = (
        recommendations["spend"] * (recommendations["budget_change_pct"] / 100.0)
    )
    net_budget_shift = float(spend_deltas.sum())
    waste_eliminated = float(
        abs(spend_deltas[recommendations["action"] == "Reduce budget"].sum())
    )
    growth_capital_deployed = float(
        spend_deltas[recommendations["action"] == "Increase budget"].sum()
    )

    return {
        "total_campaigns_evaluated": total_campaigns,
        "action_breakdown": action_counts,
        "total_ad_spend": round(total_spend, 2),
        "total_contribution_profit": round(total_profit, 2),
        "net_budget_reallocation": round(net_budget_shift, 2),
        "ad_waste_eliminated": round(waste_eliminated, 2),
        "growth_capital_deployed": round(growth_capital_deployed, 2),
        "inventory_alerts_count": int(
            (recommendations["action"] == "Hold budget").sum()
        ),
    }


# ---------------------------------------------------------------------------
# CLI Entrypoint & Verification Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Load raw dataset and generate campaign-SKU summary
    df = load_data()
    summary = campaign_summary(df)

    # Generate prioritized recommendations
    recommendations = generate_recommendations(summary)

    print("Campaign-SKU decision rows:", len(recommendations))

    print("\nTop budget actions:")
    print(
        recommendations[
            [
                "ad_platform",
                "campaign_id",
                "sku_id",
                "spend",
                "revenue",
                "contribution_profit",
                "profit_roas",
                "avg_margin_pct",
                "avg_inventory",
                "opportunity_score",
                "action",
                "budget_change_pct",
                "confidence",
                "reason",
            ]
        ]
        .head(20)
        .to_string(index=False)
    )

    if not recommendations.empty:
        print("\nSample recommendation payload (JSON-Ready):")
        sample_row = recommendations.iloc[0]
        sample_payload = build_recommendation_payload(sample_row)
        print(json.dumps(sample_payload, indent=2))

        print("\nPortfolio Impact Summary:")
        summary_stats = portfolio_summary(recommendations)
        for k, v in summary_stats.items():
            print(f"  {k}: {v}")