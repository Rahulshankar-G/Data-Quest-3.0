import json
import os
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv, find_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

# Load environment variables
load_dotenv(find_dotenv())

api_key = os.getenv("OPENAI_API_KEY")
MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

if api_key:
    client = OpenAI(api_key=api_key)
else:
    client = None


# --- 1. PYDANTIC SCHEMA DEFINITIONS (Section 4 & 9) ---
class Metric(BaseModel):
    name: str
    current: float
    previous: Optional[float] = None
    change_pct: Optional[float] = None
    unit: str


class ActionReadyInsight(BaseModel):
    entity_type: str = Field(description="campaign | product | channel | customer_segment")
    entity_id: str
    performance_status: str = Field(description="high_performing | underperforming | watchlist | constrained")
    priority: str = Field(description="critical | high | medium | low")
    headline: str
    action: str
    budget_change_pct: float
    key_metrics: List[Metric]
    evidence: List[str]
    likely_drivers: List[str]
    next_steps: List[str]
    risks_and_guardrails: List[str]
    confidence: float
    data_limitations: List[str]


class DiagnosisPayload(BaseModel):
    source: str = Field(description="'openai' or 'fallback'")
    model: str
    insights: List[ActionReadyInsight]
    portfolio_summary_note: str


# --- 2. FALLBACK GENERATOR (Section 10) ---
def fallback_diagnosis(alerts: List[Dict[str, Any]], recommendations: List[Dict[str, Any]], reason: str) -> Dict[str, Any]:
    top_rec = recommendations[0] if recommendations else {}
    top_alert = alerts[0] if alerts else {}

    fallback_insight = ActionReadyInsight(
        entity_type="campaign",
        entity_id=str(top_rec.get("campaign_id", top_alert.get("campaign_id", "UNKNOWN"))),
        performance_status="underperforming" if top_alert.get("severity") == "Critical" else "watchlist",
        priority=str(top_alert.get("severity", "medium")).lower(),
        headline=f"Fallback Diagnostic: Action required for campaign {top_rec.get('campaign_id', 'UNKNOWN')}.",
        action=str(top_rec.get("action", "Hold budget")),
        budget_change_pct=float(top_rec.get("budget_change_pct", 0.0)),
        key_metrics=[
            Metric(
                name="ROAS",
                current=float(top_alert.get("roas", 0.0)),
                previous=float(top_alert.get("prev_roas", 0.0)) if "prev_roas" in top_alert else None,
                change_pct=float(top_alert.get("roas_change_pct", 0.0)),
                unit="x"
            )
        ],
        evidence=["Deterministic rule engine triggered alert."],
        likely_drivers=["Hypothesis: Performance fluctuation detected by metrics engine."],
        next_steps=["Review campaign metrics in dashboard before applying budget changes."],
        risks_and_guardrails=["Automated rule output without LLM narrative expansion."],
        confidence=0.70,
        data_limitations=[f"Fallback engine active: {reason}"]
    )

    return DiagnosisPayload(
        source="fallback",
        model="deterministic_rules",
        insights=[fallback_insight],
        portfolio_summary_note="Fallback mode active. Operating on rule-based triggers."
    ).model_dump()


# --- 3. MAIN DIAGNOSIS GENERATOR (Section 7 & 8) ---
def generate_llm_diagnosis(
    alerts: List[Dict[str, Any]], 
    recommendations: List[Dict[str, Any]]
) -> Dict[str, Any]:
    if not client:
        return fallback_diagnosis(alerts, recommendations, "OPENAI_API_KEY missing.")

    system_prompt = (
        "You are ProfitPilot AI, a D2C performance-analysis copilot.\n"
        "Use ONLY values and entity names present in the supplied JSON.\n"
        "Never calculate replacement KPIs, invent a campaign, product, channel, segment, cause, or benchmark.\n"
        "Treat all drivers as hypotheses unless directly measured.\n"
        "Do not change the deterministic action or budget_change_pct.\n"
        "Identify high-performing and underperforming entities only from supplied labels and metrics.\n"
        "Explain the decision using concise business language.\n"
        "If customer-segment data is absent, state that segment-level insight is unavailable."
    )

    user_prompt = f"""
--- DETECTED CAMPAIGN ANOMALIES ---
{json.dumps(alerts[:5], indent=2)}

--- RECOMMENDATION PAYLOADS ---
{json.dumps(recommendations[:5], indent=2)}

Generate structured insights matching the required schema for the top entities.
"""

    try:
        # Use Structured Outputs with Pydantic parsing
        completion = client.beta.chat.completions.parse(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=ActionReadyInsight,
            temperature=0.2,
        )

        insight = completion.choices[0].message.parsed

        return DiagnosisPayload(
            source="openai",
            model=MODEL_NAME,
            insights=[insight],
            portfolio_summary_note="Successfully generated structured AI diagnostic brief."
        ).model_dump()

    except Exception as e:
        return fallback_diagnosis(alerts, recommendations, str(e))
