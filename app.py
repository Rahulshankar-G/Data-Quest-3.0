import streamlit as st
import pandas as pd
import requests
import plotly.express as px
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import shutil
import socket
import subprocess
import signal
import sys
import time
import webbrowser
from uuid import uuid4
from dotenv import load_dotenv
from streamlit.runtime.scriptrunner import get_script_run_ctx

load_dotenv()

APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR / "frontend"


def _next_available_port(start_port: int, host: str = "127.0.0.1") -> int:
    port = int(start_port)
    for _ in range(50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, port))
                return port
            except OSError:
                port += 1
    raise RuntimeError(f"No free port found starting from {start_port} on {host}.")


API_PORT = int(os.getenv("PROFITPILOT_API_PORT", _next_available_port(8000)))
FRONTEND_PORT = int(os.getenv("ADPILOT_FRONTEND_PORT", _next_available_port(3000)))
FASTAPI_BASE_URL = os.getenv("PROFITPILOT_API_URL", f"http://127.0.0.1:{API_PORT}").rstrip("/")
FRONTEND_URL = os.getenv("ADPILOT_FRONTEND_URL", f"http://127.0.0.1:{FRONTEND_PORT}").rstrip("/")
API_HEADERS = (
    {"X-API-Key": os.getenv("PROFITPILOT_API_KEY", "").strip()}
    if os.getenv("PROFITPILOT_API_KEY", "").strip()
    else {}
)


def _backend_is_running() -> bool:
    try:
        response = requests.get(f"{FASTAPI_BASE_URL}/", headers=API_HEADERS, timeout=0.5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _frontend_is_running() -> bool:
    try:
        response = requests.get(FRONTEND_URL, timeout=0.5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _launch_application() -> None:
    if _frontend_is_running():
        if not _backend_is_running():
            raise RuntimeError(
                "The AdPilot frontend is already running but its API is offline. "
                "Stop the existing frontend before restarting with app.py."
            )
        if not webbrowser.open_new_tab(FRONTEND_URL):
            print(f"Open AdPilot at {FRONTEND_URL}")
        return

    backend_process = None
    frontend_process = None
    previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def handle_termination(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_termination)
    if not _backend_is_running():
        backend_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "api:app",
                "--host",
                os.getenv("PROFITPILOT_API_HOST", "0.0.0.0"),
                "--port",
                str(API_PORT),
            ],
            cwd=APP_DIR,
        )

    try:
        for _ in range(40):
            if _backend_is_running():
                break
            if backend_process is not None and backend_process.poll() is not None:
                raise RuntimeError("The FastAPI backend exited before becoming ready.")
            time.sleep(0.5)
        else:
            raise RuntimeError("The FastAPI backend did not become ready on port 8000.")

        if not (FRONTEND_DIR / "node_modules").is_dir():
            raise RuntimeError(
                "The AdPilot frontend dependencies are not installed. Run "
                "`cd frontend && npm install`, then retry `python app.py`."
            )
        frontend_env = os.environ.copy()
        frontend_env["ADPILOT_API_URL"] = FASTAPI_BASE_URL
        frontend_env["ADPILOT_API_KEY"] = os.getenv(
            "ADPILOT_API_KEY",
            os.getenv("PROFITPILOT_API_KEY", ""),
        )
        frontend_port = str(FRONTEND_PORT)
        npm_binary = shutil.which("npm") or shutil.which("npm.cmd")
        if npm_binary is None:
            raise RuntimeError("Node.js and npm are required to launch the AdPilot frontend.")
        frontend_script = "start" if (FRONTEND_DIR / ".next" / "BUILD_ID").is_file() else "dev"
        frontend_command = [
            npm_binary,
            "run",
            frontend_script,
            "--",
            "--hostname=0.0.0.0",
            "--port",
            frontend_port,
        ]
        frontend_process = subprocess.Popen(
            frontend_command,
            cwd=FRONTEND_DIR,
            env=frontend_env,
        )
        for _ in range(120):
            if frontend_process.poll() is not None:
                raise RuntimeError("The Next.js frontend exited before becoming ready.")
            try:
                if requests.get(FRONTEND_URL, timeout=1.5).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("The AdPilot frontend did not become ready.")

        if (
            os.getenv("APP_ENV", "development").lower() != "production"
            and not webbrowser.open_new_tab(FRONTEND_URL)
        ):
            print(f"Open AdPilot at {FRONTEND_URL}")
        return_code = frontend_process.wait()
        if return_code != 0:
            raise RuntimeError(f"The Next.js frontend exited with status {return_code}.")
    finally:
        if frontend_process is not None and frontend_process.poll() is None:
            frontend_process.terminate()
            try:
                frontend_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                frontend_process.kill()
        if backend_process is not None and backend_process.poll() is None:
            backend_process.terminate()
            try:
                backend_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                backend_process.kill()
        signal.signal(signal.SIGTERM, previous_sigterm_handler)


if __name__ == "__main__" and get_script_run_ctx(suppress_warning=True) is None:
    _launch_application()
    raise SystemExit(0)

# -----------------------------------------------------------------------------
# 1. Page Configuration & Custom CSS
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="ProfitPilot AI - D2C Intelligence",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)
st.markdown("""
    <style>
    .stApp { background: #f4f7fb; color: #172033; }
    [data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #e5eaf2; }
    [data-testid="stHeader"] { background: rgba(244, 247, 251, 0.94); }
    h1, h2, h3 { color: #172033; letter-spacing: -0.025em; }
    .block-container { padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1480px; }
    div[data-testid="stMetric"] {
        background: #ffffff; border: 1px solid #e5eaf2; border-radius: 14px;
        padding: 18px 20px; box-shadow: 0 4px 16px rgba(20, 35, 60, 0.05);
    }
    div[data-testid="stMetricLabel"] { color: #69758a; }
    div[data-testid="stMetricValue"] { color: #172033; }
    div[data-testid="stAlert"] { border-radius: 12px; }
    div[data-testid="stExpander"] { background: #ffffff; border: 1px solid #e5eaf2; border-radius: 12px; }
    div[data-testid="stDataFrame"] { border: 1px solid #e5eaf2; border-radius: 12px; overflow: hidden; }
    button[kind="primary"] { background: #155eef; border-color: #155eef; }
    div[role="radiogroup"] { gap: 0.4rem; }
    div[role="radiogroup"] label {
        background: #ffffff; border: 1px solid #e5eaf2; border-radius: 10px;
        padding: 0.55rem 0.9rem; font-weight: 600;
    }
    div[role="radiogroup"] label:has(input:checked) {
        background: #eef4ff; border-color: #84adff; color: #1849a9;
    }
    [data-testid="stSidebar"] [data-testid="stMetric"] { box-shadow: none; }
    </style>
""", unsafe_allow_html=True)

if "decision_log" not in st.session_state:
    st.session_state.decision_log = []

# -----------------------------------------------------------------------------
# 2. Sidebar Navigation
# -----------------------------------------------------------------------------
st.sidebar.title("AdPilot")
st.sidebar.caption("D2C Growth & Analytics Platform")
st.sidebar.markdown("---")

# Helper function to connect with FastAPI backend
def fetch_data(endpoint: str):
    try:
        clean_endpoint = endpoint.lstrip("/") if endpoint else ""
        url = f"{FASTAPI_BASE_URL}/{clean_endpoint}" if clean_endpoint else FASTAPI_BASE_URL
        response = requests.get(url, headers=API_HEADERS, timeout=5)
        if response.status_code == 200:
            return response.json()
    except Exception:
        return None
    return None


def post_decision(record):
    try:
        response = requests.post(
            f"{FASTAPI_BASE_URL}/api/decisions",
            json=record,
            headers=API_HEADERS,
            timeout=5,
        )
        return response.status_code in (200, 201)
    except requests.RequestException:
        return False


def post_outcome(decision_id: str, outcome: dict):
    try:
        response = requests.post(
            f"{FASTAPI_BASE_URL}/api/decisions/{decision_id}/outcome",
            json=outcome,
            headers=API_HEADERS,
            timeout=5,
        )
        if response.ok:
            return response.json()
        return {"error": response.json().get("detail", response.text)}
    except requests.RequestException as exc:
        return {"error": str(exc)}


def post_decision_action(decision_id: str, action: str):
    try:
        response = requests.post(
            f"{FASTAPI_BASE_URL}/api/decisions/{decision_id}/{action}",
            json={} if action == "execute" else None,
            headers=API_HEADERS,
            timeout=10,
        )
        if response.ok:
            return response.json()
        return {"error": response.json().get("detail", response.text)}
    except requests.RequestException as exc:
        return {"error": str(exc)}


def post_integration_sync(days: int):
    try:
        response = requests.post(
            f"{FASTAPI_BASE_URL}/api/integrations/sync",
            json={"days": days},
            headers=API_HEADERS,
            timeout=180,
        )
        if response.ok:
            return response.json()
        return {"error": response.json().get("detail", response.text)}
    except requests.RequestException as exc:
        return {"error": str(exc)}


# API Connection status badge
api_health = fetch_data("")
if api_health:
    st.sidebar.success("Backend connected")
else:
    st.sidebar.warning("Backend offline")

persisted_decisions = fetch_data("api/decisions")
if isinstance(persisted_decisions, list):
    st.session_state.decision_log = persisted_decisions
learning_status = fetch_data("api/learning/status")
if isinstance(learning_status, dict):
    st.sidebar.caption(
        f"Learning history: {learning_status.get('training_examples', 0)} "
        f"saved decisions"
    )
    measured_status = learning_status.get("measured_outcomes", {})
    if isinstance(measured_status, dict):
        st.sidebar.caption(
            f"Measured outcomes: {measured_status.get('training_examples', 0)}"
        )
profile = fetch_data("api/data-profile")
if isinstance(profile, dict):
    st.sidebar.markdown("**Data coverage**")
    st.sidebar.caption(
        f"{profile.get('rows', 0):,} records · "
        f"{profile.get('dimensions', {}).get('ad_platform', 0)} platforms"
    )
    st.sidebar.caption(
        f"{profile.get('date_start', '—')} to {profile.get('date_end', '—')}"
    )
    if not profile.get("creative_data_available", False):
        st.sidebar.caption("Creative-level data is not in the current data source.")
integration_summary = fetch_data("api/integrations/status")
if isinstance(integration_summary, dict):
    st.sidebar.markdown("**Live connections**")
    st.sidebar.caption(
        f"Shopify: {'configured' if integration_summary.get('shopify_configured') else 'not configured'}"
    )
    st.sidebar.caption(
        f"Google Ads: {'configured' if integration_summary.get('google_ads_configured') else 'not configured'}"
    )

st.markdown("## AdPilot")
st.caption("Advertising intelligence · profitability · inventory-aware decisions")
page = st.radio(
    "Main navigation",
    [
        "Overview",
        "Performance",
        "Anomaly Intelligence",
        "Recommendations",
        "Decision History",
        "Integrations",
    ],
    horizontal=True,
    label_visibility="collapsed",
)
st.divider()


# -----------------------------------------------------------------------------
# 3. Dynamic Page Content
# -----------------------------------------------------------------------------

# PAGE 1: EXECUTIVE OVERVIEW
if page == "Overview":
    st.title("Portfolio overview")
    st.caption("A concise view of portfolio profitability and marketing efficiency.")

    metrics_response = fetch_data("api/metrics/summary")
    metrics_df = pd.DataFrame(metrics_response) if isinstance(metrics_response, list) else pd.DataFrame()
    metrics = None
    if not metrics_df.empty:
        total_revenue = float(metrics_df["revenue"].sum()) if "revenue" in metrics_df else 0.0
        total_profit = (
            float(metrics_df["contribution_profit"].sum())
            if "contribution_profit" in metrics_df
            else 0.0
        )
        total_spend = float(metrics_df["spend"].sum()) if "spend" in metrics_df else 0.0
        metrics = {
            "total_revenue": total_revenue,
            "net_profit": total_profit,
            "profit_margin": total_profit / total_revenue * 100 if total_revenue else 0.0,
            "roas": total_revenue / total_spend if total_spend else 0.0,
        }
    if metrics is None:
        st.warning("Live performance metrics could not be loaded. Start the backend and retry.")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total revenue", f"${metrics['total_revenue']:,.0f}" if metrics else "—")
    with col2:
        st.metric("Contribution profit", f"${metrics['net_profit']:,.0f}" if metrics else "—")
    with col3:
        st.metric("Profit margin", f"{metrics['profit_margin']:.1f}%" if metrics else "—")
    with col4:
        st.metric("Blended ROAS", f"{metrics['roas']:.2f}x" if metrics else "—")

    st.markdown("### Campaign profitability")
    required_columns = {"campaign_id", "revenue", "contribution_profit"}
    if required_columns.issubset(metrics_df.columns):
        campaign_data = (
            metrics_df.nlargest(10, "revenue")
            .sort_values("revenue", ascending=False)
        )
        fig = px.bar(
            campaign_data,
            x="campaign_id",
            y=["revenue", "contribution_profit"],
            barmode="group",
            labels={
                "campaign_id": "Campaign",
                "value": "Amount ($)",
                "variable": "Metric",
            },
            template="plotly_white",
            color_discrete_sequence=["#155eef", "#12b76a"],
        )
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Campaign profitability chart is unavailable until live metrics are loaded.")


# PAGE 2: PERFORMANCE ANALYTICS
elif page == "Performance":
    st.title("Performance by business dimension")
    st.caption("Compare marketing efficiency and profitability across the dimensions in the source data.")

    breakdowns = fetch_data("api/breakdowns")
    breakdown_tabs = [
        ("ad_platform", "Ad platforms"),
        ("channel", "Sales channels"),
        ("product_category", "Product categories"),
        ("region", "Regions"),
        ("customer_segment", "Customer segments"),
    ]
    if not isinstance(breakdowns, dict):
        st.error("Cross-channel breakdowns could not be loaded from the backend.")
    else:
        tabs = st.tabs([label for _, label in breakdown_tabs])
        for tab, (dimension, label) in zip(tabs, breakdown_tabs):
            with tab:
                st.subheader(f"Performance by {label.lower()}")
                rows = breakdowns.get(dimension, [])
                dimension_df = pd.DataFrame(rows)
                if dimension_df.empty:
                    st.info(f"No {label.lower()} data is available in the source file.")
                    continue
                dimension_df = dimension_df.rename(columns={dimension: "Dimension"})
                chart_df = dimension_df.nlargest(15, "revenue")
                fig_breakdown = px.bar(
                    chart_df,
                    x="Dimension",
                    y=["revenue", "spend", "contribution_profit"],
                    barmode="group",
                    labels={"value": "Amount ($)", "variable": "Metric"},
                    template="plotly_white",
                    color_discrete_sequence=["#155eef", "#7f56d9", "#12b76a"],
                )
                st.plotly_chart(fig_breakdown, width="stretch")
                st.dataframe(
                    dimension_df.sort_values("revenue", ascending=False),
                    width="stretch",
                    hide_index=True,
                )


# PAGE 3: ANOMALY DETECTION
elif page == "Anomaly Intelligence":
    st.title("Anomaly intelligence")
    st.caption(
        "Find unusual campaign behavior and estimate deterioration risk at its next recorded observation."
    )

    anomalies = fetch_data("api/anomalies?limit=500")
    forecast = fetch_data("api/anomaly-forecast")

    if not isinstance(anomalies, list) or not isinstance(forecast, dict):
        st.error("Anomaly analysis could not be loaded. Confirm the API is online and retry.")
    else:
        alerts_df = pd.DataFrame(anomalies)
        predictions = forecast.get("predictions", [])
        prediction_df = pd.DataFrame(predictions)
        evaluation = forecast.get("evaluation") or {}
        profile_period = forecast.get("dataset_period", {})

        critical_count = int((prediction_df.get("risk_level", pd.Series(dtype=str)) == "Critical").sum())
        high_count = int((prediction_df.get("risk_level", pd.Series(dtype=str)) == "High").sum())
        model_col, current_col, critical_col, high_col = st.columns(4)
        model_col.metric(
            "Predictive model",
            "Ready" if forecast.get("trained") else "Not ready",
        )
        current_col.metric("Detected historical outliers", len(alerts_df))
        critical_col.metric("Critical forecast risk", critical_count)
        high_col.metric("High forecast risk", high_count)

        st.info(
            f"**Model:** {forecast.get('model', '—')} · "
            f"**Forecast horizon:** {forecast.get('horizon', '—')}. "
            f"Data available from {profile_period.get('start', '—')} to "
            f"{profile_period.get('end', '—')}."
        )
        data_end = pd.to_datetime(profile_period.get("end"), errors="coerce")
        if pd.notna(data_end) and data_end < pd.Timestamp.now().normalize() - pd.Timedelta(days=365):
            st.warning(
                "The source data is more than one year old. Forecasts describe the next "
                "recorded campaign observation in this historical file, not current live campaign risk."
            )
        st.caption(forecast.get("message", ""))

        if evaluation:
            eval_accuracy, eval_precision, eval_recall, eval_date = st.columns(4)
            eval_accuracy.metric("Holdout balanced accuracy", f"{evaluation.get('balanced_accuracy', 0):.0%}")
            eval_precision.metric("Holdout precision", f"{evaluation.get('precision', 0):.0%}")
            eval_recall.metric("Holdout recall", f"{evaluation.get('recall', 0):.0%}")
            eval_date.metric("Time-ordered test split", evaluation.get("time_split", "—"))
            st.caption(
                f"Evaluation used {evaluation.get('test_examples', 0):,} time-ordered holdout examples. "
                "Risk scores are model estimates, not calibrated probabilities."
            )
        elif not forecast.get("trained"):
            st.warning(forecast.get("message", "Insufficient history to train the early-warning model."))

        tab_forecast, tab_detected = st.tabs(
            ["Next-observation risk", "Detected historical anomalies"]
        )
        with tab_forecast:
            st.subheader("Campaigns with the highest predicted risk")
            if not prediction_df.empty:
                risk_levels = ["Critical", "High", "Moderate", "Low"]
                selected_levels = st.multiselect(
                    "Show risk levels",
                    options=risk_levels,
                    default=["Critical", "High", "Moderate"],
                )
                filtered_predictions = prediction_df[
                    prediction_df["risk_level"].isin(selected_levels)
                ].copy()
                filtered_predictions = filtered_predictions.sort_values(
                    "risk_score", ascending=False
                )
                if not filtered_predictions.empty:
                    chart_data = filtered_predictions.head(15).sort_values("risk_score")
                    fig_risk = px.bar(
                        chart_data,
                        x="risk_score",
                        y="campaign_id",
                        color="risk_level",
                        orientation="h",
                        hover_data=["ad_platform", "likely_drivers"],
                        labels={
                            "risk_score": "Estimated risk score (0–100)",
                            "campaign_id": "Campaign",
                            "risk_level": "Risk band",
                        },
                        color_discrete_map={
                            "Critical": "#b42318",
                            "High": "#dc6803",
                            "Moderate": "#fdb022",
                            "Low": "#12b76a",
                        },
                        template="plotly_white",
                    )
                    fig_risk.update_layout(showlegend=True, xaxis_range=[0, 100])
                    st.plotly_chart(fig_risk, width="stretch")
                    display_columns = [
                        column for column in (
                            "day",
                            "ad_platform",
                            "campaign_id",
                            "risk_level",
                            "risk_score",
                            "roas",
                            "conversion_rate",
                            "avg_stock",
                            "likely_drivers",
                        )
                        if column in filtered_predictions
                    ]
                    st.dataframe(
                        filtered_predictions[display_columns],
                        width="stretch",
                        hide_index=True,
                    )
                else:
                    st.success("No campaigns fall within the selected risk bands.")
            else:
                st.warning("The early-warning model has no campaign predictions to show.")

        with tab_detected:
            st.subheader("Unusual historical campaign observations")
            st.caption(
                "An Isolation Forest reviews spend, returns, conversion, margins, stock, "
                "discount, competition, and recent changes together. Driver text is evidence "
                "for investigation, not proof of causality."
            )
            if not alerts_df.empty:
                platform_choices = ["All platforms"] + sorted(
                    alerts_df["ad_platform"].dropna().unique().tolist()
                )
                selected_platform = st.selectbox("Filter platform", platform_choices)
                if selected_platform != "All platforms":
                    alerts_df = alerts_df[alerts_df["ad_platform"] == selected_platform]
                st.dataframe(
                    alerts_df.sort_values("anomaly_risk_score", ascending=False),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.success("No observations were flagged by the current anomaly detector.")


# PAGE 4: AI RECOMMENDATIONS
elif page == "Recommendations":
    st.title("🤖 Decision Workspace")
    st.caption("Review evidence-backed recommendations and record a human decision.")

    response = fetch_data("api/recommendations")
    recommendation_list = []
    recommendation_learning_status = learning_status
    if isinstance(response, dict):
        recommendation_list = response.get("recommendations", [])
        recommendation_learning_status = response.get("learning_status", learning_status)
    elif isinstance(response, list):
        recommendation_list = response

    if recommendation_list:
        selected_index = st.selectbox(
            "Campaign recommendation",
            options=range(len(recommendation_list)),
            format_func=lambda index: (
                f"{recommendation_list[index].get('campaign_id', 'Campaign')} · "
                f"{recommendation_list[index].get('ad_platform', 'Platform')} · "
                f"{recommendation_list[index].get('sku_id', 'SKU')} · "
                f"{recommendation_list[index].get('action', 'Review')}"
            ),
        )
        recommendation = recommendation_list[selected_index]
    else:
        recommendation = None
        st.error("No live recommendations are available. Check the backend and source data.")

    if isinstance(recommendation, pd.Series):
        recommendation = recommendation.to_dict()

    if recommendation:
        action = recommendation.get("action", "Maintain budget")
        change_pct = recommendation.get("budget_change_pct") or 0
        reason = recommendation.get("reason", "No reason supplied")
        confidence = recommendation.get("confidence")

        st.subheader(f"{recommendation.get('campaign_id', 'Campaign')} · {recommendation.get('sku_id', 'SKU')}")
        platform_col, action_col, change_col, confidence_col = st.columns(4)
        platform_col.metric("Channel", recommendation.get("ad_platform") or "—")
        action_col.metric("Recommended action", action)
        change_col.metric("Budget change", f"{change_pct:+g}%")
        confidence_col.metric(
            "Recommendation confidence",
            f"{float(confidence):.0%}" if confidence is not None else "—",
        )
        st.info(reason)

        if recommendation.get("supporting_evidence"):
            with st.expander("Evidence and operational context", expanded=True):
                for bullet in recommendation.get("supporting_evidence", []):
                    st.markdown(f"- {bullet}")
                if recommendation.get("key_risk"):
                    st.markdown(f"**Key risk:** {recommendation.get('key_risk')}")
                if recommendation.get("operational_next_step"):
                    st.markdown(f"**Suggested next step:** {recommendation.get('operational_next_step')}")

        prediction = recommendation.get("human_feedback_prediction")
        if prediction:
            st.success(
                f"Historical reviewer preference: **{prediction['predicted_decision']}** "
                f"(model score {prediction['probabilities'].get(prediction['predicted_decision'], 0):.0%}; "
                f"{prediction['training_examples']} saved decisions)."
            )
        outcome_prediction = recommendation.get("profitability_outcome_prediction")
        if outcome_prediction:
            st.warning(
                f"Historical profitability signal: **{outcome_prediction['predicted_decision']}** "
                f"(model score "
                f"{outcome_prediction['probabilities'].get(outcome_prediction['predicted_decision'], 0):.0%}; "
                f"{outcome_prediction['training_examples']} measured outcomes). "
                "This is an association, not a causal effect estimate."
            )
        elif isinstance(recommendation_learning_status, dict):
            st.caption(recommendation_learning_status.get("message", "Learning model is gathering reviewer decisions."))
            if recommendation_learning_status.get("ready") is False:
                st.progress(
                    min(
                        recommendation_learning_status.get("training_examples", 0)
                        / recommendation_learning_status.get("minimum_examples", 6),
                        1.0,
                    )
                )

        st.divider()
        st.subheader("Record reviewer decision")
        st.caption("This is an approval/audit record only. It does not modify a live advertising budget.")

        human_decision = st.radio(
            "Reviewer decision",
            ["Approve", "Reject", "Monitor"],
            horizontal=True,
            key="approval_decision",
        )
        reviewer_notes = st.text_area(
            "Reviewer notes (optional)",
            placeholder="Example: Approve the budget cut and review creative-level CPA tomorrow.",
            key="approval_notes",
        )

        if st.button("Save Decision", type="primary", key="save_decision"):
            record = {
                "decision_id": str(uuid4()),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "campaign_id": recommendation.get("campaign_id"),
                "sku_id": recommendation.get("sku_id"),
                "ad_platform": recommendation.get("ad_platform"),
                "severity": recommendation.get("severity"),
                "proposed_action": action,
                "budget_change_pct": change_pct,
                "human_decision": human_decision,
                "reviewer_notes": reviewer_notes.strip(),
                "confidence": confidence,
                "decision_source": "human_in_the_loop",
                "model_features": {
                    key: recommendation.get(key)
                    for key in (
                        "action",
                        "ad_platform",
                        "decision_priority",
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
                },
            }
            saved = post_decision(record)
            if saved:
                refreshed_decisions = fetch_data("api/decisions")
                if isinstance(refreshed_decisions, list):
                    st.session_state.decision_log = refreshed_decisions
                else:
                    st.session_state.decision_log.append(record)
                updated_learning_status = fetch_data("api/learning/status")
                if isinstance(updated_learning_status, dict):
                    st.session_state.learning_status = updated_learning_status
                    learning_status = updated_learning_status
                st.success(f"Decision saved: {human_decision}. The learner will include this label on its next prediction.")
            else:
                st.session_state.decision_log.append(record)
                st.error("Backend save failed. This decision is only in this browser session and will not train the persistent model.")
    else:
        st.info("Select or load a recommendation before approving a decision.")


elif page == "Decision History":
    st.title("Decision history")
    st.caption("Persisted reviewer labels provide examples for the preference model.")
    persisted_decisions = fetch_data("api/decisions")
    if isinstance(persisted_decisions, list):
        st.session_state.decision_log = persisted_decisions
    if isinstance(learning_status, dict):
        total = learning_status.get("training_examples", 0)
        minimum = learning_status.get("minimum_examples", 6)
        ready = learning_status.get("ready", False)
        measured_status = learning_status.get("measured_outcomes", {})
        first, second, third, fourth = st.columns(4)
        first.metric("Saved decisions", total)
        second.metric("Model status", "Learning" if ready else "Collecting feedback")
        third.metric("Training threshold", f"{min(total, minimum)} / {minimum}")
        fourth.metric(
            "Measured outcomes",
            f"{measured_status.get('training_examples', 0)} / "
            f"{measured_status.get('minimum_examples', 30)}",
        )
        st.caption(learning_status.get("message", ""))
        if isinstance(measured_status, dict):
            st.caption(measured_status.get("message", ""))
    if st.session_state.decision_log:
        st.subheader("Execution simulator")
        st.caption("Approved actions are recorded in simulator mode; no ad platform is modified.")
        for decision in st.session_state.decision_log:
            decision_id = decision.get("decision_id")
            if not decision_id:
                continue
            execution = decision.get("execution")
            if execution and execution.get("status") == "simulated":
                st.write(
                    f"{decision.get('campaign_id', 'Campaign')} · "
                    f"{execution.get('action')} · "
                    f"{execution.get('budget_change_pct', 0):+}% · Simulated"
                )
                if st.button("Roll back", key=f"rollback_{decision_id}"):
                    result = post_decision_action(decision_id, "rollback")
                    if result and "error" not in result:
                        st.session_state.decision_log = fetch_data("api/decisions") or []
                        st.rerun()
                    else:
                        st.error((result or {}).get("error", "Rollback failed."))
            elif not execution and decision.get("human_decision") == "Approve":
                if st.button(
                    f"Simulate {decision.get('proposed_action', 'action')} · "
                    f"{decision.get('campaign_id', 'campaign')}",
                    key=f"execute_{decision_id}",
                ):
                    result = post_decision_action(decision_id, "execute")
                    if result and "error" not in result:
                        st.session_state.decision_log = fetch_data("api/decisions") or []
                        st.rerun()
                    else:
                        st.error((result or {}).get("error", "Execution failed."))
            elif execution:
                st.caption(
                    f"{decision.get('campaign_id', 'Campaign')} · "
                    f"execution {execution.get('status', 'unknown')}"
                )

        eligible_decisions = [
            record for record in st.session_state.decision_log
            if record.get("decision_id") and not record.get("outcome")
        ]
        if eligible_decisions:
            with st.expander("Record measured post-decision outcome"):
                st.caption(
                    "Enter results after the campaign has had time to respond. "
                    "Contribution profit is calculated as revenue − COGS − ad spend."
                )
                selected_decision_id = st.selectbox(
                    "Decision to evaluate",
                    options=[record["decision_id"] for record in eligible_decisions],
                    format_func=lambda decision_id: next(
                        (
                            f"{item.get('campaign_id', 'Campaign')} · "
                            f"{item.get('proposed_action', 'Action')} · "
                            f"{item.get('human_decision', 'Review')} · "
                            f"{item.get('timestamp', '')}"
                            for item in eligible_decisions
                            if item["decision_id"] == decision_id
                        ),
                        decision_id,
                    ),
                )
                with st.form("decision_outcome_form"):
                    measurement_period = st.date_input(
                        "Measurement period",
                        value=(date.today() - timedelta(days=7), date.today()),
                    )
                    outcome_col1, outcome_col2, outcome_col3 = st.columns(3)
                    actual_spend = outcome_col1.number_input(
                        "Actual ad spend ($)", min_value=0.0, step=100.0
                    )
                    actual_revenue = outcome_col2.number_input(
                        "Actual sales revenue ($)", min_value=0.0, step=100.0
                    )
                    actual_cogs = outcome_col3.number_input(
                        "Actual cost of goods sold ($)", min_value=0.0, step=50.0
                    )
                    outcome_notes = st.text_input("Outcome notes (optional)")
                    outcome_submitted = st.form_submit_button(
                        "Save measured outcome",
                        type="primary",
                    )
                if outcome_submitted:
                    if not isinstance(measurement_period, tuple) or len(measurement_period) != 2:
                        st.error("Select both start and end dates for the measurement period.")
                    else:
                        result = post_outcome(
                            selected_decision_id,
                            {
                                "measured_from": measurement_period[0].isoformat(),
                                "measured_to": measurement_period[1].isoformat(),
                                "actual_spend": actual_spend,
                                "actual_revenue": actual_revenue,
                                "actual_cogs": actual_cogs,
                                "notes": outcome_notes,
                            },
                        )
                        if result and "error" not in result:
                            refreshed_decisions = fetch_data("api/decisions")
                            if isinstance(refreshed_decisions, list):
                                st.session_state.decision_log = refreshed_decisions
                            updated_status = fetch_data("api/learning/status")
                            if isinstance(updated_status, dict):
                                learning_status = updated_status
                            st.success("Measured outcome saved for future profitability-model training.")
                            st.rerun()
                        else:
                            st.error(
                                result.get("error", "The measured outcome could not be saved.")
                                if result
                                else "The measured outcome could not be saved."
                            )
        st.dataframe(
            pd.DataFrame(st.session_state.decision_log),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No decisions recorded in this session.")

elif page == "Integrations":
    st.title("Live data integrations")
    st.caption(
        "Connect Shopify product costs and inventory with Google Ads campaign metrics. "
        "Credentials are read from server environment variables and are never shown here."
    )
    if not isinstance(integration_summary, dict):
        st.error("Integration status is unavailable. Check the API connection and access configuration.")
    else:
        provider_col1, provider_col2 = st.columns(2)
        with provider_col1:
            if integration_summary.get("shopify_configured"):
                st.success("Shopify credentials configured")
            else:
                st.warning("Shopify credentials are not configured")
        with provider_col2:
            if integration_summary.get("google_ads_configured"):
                st.success("Google Ads credentials configured")
            else:
                st.warning("Google Ads credentials are not configured")

        if integration_summary.get("campaign_sku_mapping_configured"):
            st.success("Campaign-to-SKU mapping is configured")
        else:
            st.warning(
                "An explicit campaign-to-SKU mapping is required. Campaigns are not "
                "matched to products by guessed campaign names."
            )
        if integration_summary.get("missing_configuration"):
            st.markdown("**Configuration still needed**")
            st.code("\n".join(integration_summary["missing_configuration"]), language="text")
            st.caption(
                "Set these values in your local `.env` file or AWS Secrets Manager. "
                "Never paste access tokens into the dashboard or commit them to source control."
            )

        if integration_summary.get("live_data_active"):
            st.success("Live data is currently used by the analysis and recommendation endpoints.")
        else:
            st.info("The dashboard is currently using its sample dataset.")

        last_sync = integration_summary.get("last_sync")
        if last_sync:
            st.markdown("**Last successful sync**")
            order_metric_col1, order_metric_col2, order_metric_col3 = st.columns(3)
            currency = last_sync.get("currency_code", "")
            order_metric_col1.metric(
                "Shopify order revenue",
                f"{currency} {last_sync.get('shopify_sales_revenue', 0):,.2f}",
            )
            order_metric_col2.metric(
                "Shopify orders",
                f"{last_sync.get('shopify_order_count', 0):,}",
            )
            order_metric_col3.metric(
                "Units sold",
                f"{last_sync.get('shopify_units_sold', 0):,}",
            )
            shopify_orders = fetch_data("api/integrations/shopify/orders")
            if isinstance(shopify_orders, dict) and shopify_orders.get("records"):
                st.markdown("**Shopify sales by SKU (separate from ad attribution)**")
                st.dataframe(
                    pd.DataFrame(shopify_orders["records"]),
                    width="stretch",
                    hide_index=True,
                )
            if last_sync.get("stale"):
                st.warning("The last successful integration sync is more than 24 hours old.")

        sync_days = st.selectbox("Provider data lookback window", [7, 14, 30, 60], index=2)
        if st.button(
            "Sync Shopify and Google Ads",
            type="primary",
            disabled=not integration_summary.get("ready_to_sync", False),
        ):
            with st.spinner("Fetching provider data and validating the campaign-to-SKU mapping..."):
                sync_result = post_integration_sync(sync_days)
            if sync_result and "error" not in sync_result:
                st.success(
                    f"Synced {sync_result['snapshot']['record_count']:,} campaign-day records. "
                    "Live data is now active."
                )
                st.rerun()
            else:
                st.error(
                    sync_result.get("error", "The data sync failed.")
                    if sync_result
                    else "The data sync failed."
                )

        st.warning(
            "Profit uses Google Ads attributed conversion value and estimates COGS from "
            "current Shopify unit cost × attributed conversions. This is not order-level "
            "attribution; verify currency, conversion actions, costs, and campaign mapping "
            "before relying on recommendations."
        )
