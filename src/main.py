import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Force project root into python path so imports resolve cleanly
root_path = Path(__file__).resolve().parent.parent
if str(root_path) not in sys.path:
    sys.path.insert(0, str(root_path))

# Import data processing & recommendation modules
from src.anomaly import detect_anomalies, build_ai_alert_payload
from src.metrics import load_data, campaign_summary
from src.recommendations import (
    generate_recommendations,
    build_recommendation_payload,
    portfolio_summary,
)
from src.diagnosis import generate_llm_diagnosis

# Define the FastAPI app object REQUIRED by uvicorn
app = FastAPI(
    title="ProfitPilot AI - Decision Engine Backend",
    description="REST API serving real-time D2C campaign anomalies, rule recommendations, and LLM diagnostics.",
    version="1.0.0",
)

# Enable CORS for Streamlit frontend interaction
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    """Health check endpoint."""
    return {
        "status": "online",
        "service": "ProfitPilot AI Backend Engine",
        "message": "FastAPI instance running successfully."
    }


@app.get("/api/anomalies")
def get_anomalies():
    """Returns flagged campaign anomalies detected by anomaly.py."""
    try:
        df = load_data()
        daily_anomalies = detect_anomalies(df)
        flagged = daily_anomalies[daily_anomalies["is_anomaly"]]

        alerts_payload = [
            build_ai_alert_payload(row) for _, row in flagged.iterrows()
        ]
        return {
            "status": "success",
            "count": len(alerts_payload),
            "data": alerts_payload,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Anomaly engine error: {str(e)}")


@app.get("/api/recommendations")
def get_recommendations():
    """Returns decision rules and portfolio impact summary."""
    try:
        df = load_data()
        summary = campaign_summary(df)
        recs_df = generate_recommendations(summary)

        recs_payload = [
            build_recommendation_payload(row) for _, row in recs_df.iterrows()
        ]
        impact_summary = portfolio_summary(recs_df)

        return {
            "status": "success",
            "portfolio_impact": impact_summary,
            "recommendations": recs_payload,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recommendation engine error: {str(e)}")


@app.get("/api/diagnose")
def get_ai_diagnosis():
    """Combines anomalies + recommendations and triggers LLM diagnostic agent."""
    try:
        df = load_data()

        # 1. Extract anomalies
        daily_anomalies = detect_anomalies(df)
        flagged = daily_anomalies[daily_anomalies["is_anomaly"]]
        alerts_payload = [
            build_ai_alert_payload(row) for _, row in flagged.head(5).iterrows()
        ]

        # 2. Extract recommendations
        summary = campaign_summary(df)
        recs_df = generate_recommendations(summary)
        recs_payload = [
            build_recommendation_payload(row) for _, row in recs_df.head(5).iterrows()
        ]

        # 3. Call LLM agent
        ai_insights = generate_llm_diagnosis(alerts_payload, recs_payload)

        return {
            "status": "success",
            "ai_diagnosis": ai_insights
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI Diagnosis engine error: {str(e)}")


@app.post("/api/execute")
def execute_action(action_payload: dict):
    """Execution endpoint for UI 1-click action buttons."""
    campaign_id = action_payload.get("campaign_id", "UNKNOWN")
    action = action_payload.get("action", "NO_ACTION")

    return {
        "status": "executed",
        "message": f"Successfully executed action '{action}' for campaign {campaign_id}.",
        "received_payload": action_payload,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)