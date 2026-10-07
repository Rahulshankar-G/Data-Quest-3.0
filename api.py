import hashlib
import json
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
import tempfile
import threading
from datetime import date, datetime, timezone
from math import isfinite
from typing import Optional, Dict, Any
from uuid import uuid4

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from starlette.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from redis.asyncio import Redis
from redis.exceptions import RedisError

load_dotenv()

from src.metrics import load_data, campaign_summary
from src.anomaly import (
    build_ai_alert_payload,
    detect_anomalies,
    forecast_campaign_anomalies,
)
from src.recommendations import generate_recommendations, build_recommendation_payload, portfolio_summary
from src.decision_learning import (
    predict_decision,
    train_decision_model,
    train_outcome_model,
)
from src.integrations import (
    IntegrationConfigurationError,
    IntegrationRequestError,
    integration_status,
    load_shopify_orders_snapshot,
    sync_integrations,
)
from src.adpilot.auth import (
    new_session_token,
    parse_bearer_token,
    session_expires_at,
    verify_password,
)
from src.adpilot.db import engine, SessionLocal
from src.adpilot.migrations import upgrade as upgrade_adpilot_schema
from src.adpilot.models import AuthSession, User
from src.adpilot.routes import router as adpilot_router
from src.adpilot.seed import seed_demo
from src.adpilot.jobs import run_scheduled_simulator_ingestion

DECISIONS_PATH = Path(__file__).resolve().parent / "data" / "decision_log.json"
DECISIONS_LOCK = threading.Lock()


def _read_decisions() -> list[dict[str, Any]]:
    if not DECISIONS_PATH.exists():
        return []
    try:
        with DECISIONS_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"Could not read decision history at {DECISIONS_PATH}: {exc}") from exc
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise RuntimeError(f"Decision history at {DECISIONS_PATH} must be a JSON array of objects.")
    return data


def _write_decisions(records: list[dict[str, Any]]) -> None:
    DECISIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=DECISIONS_PATH.parent,
            delete=False,
        ) as fh:
            temp_path = Path(fh.name)
            json.dump(records, fh, indent=2)
            fh.write("\n")
        os.replace(temp_path, DECISIONS_PATH)
    except OSError as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Could not persist decision history: {exc}") from exc


def _public_learning_status(records: list[dict[str, Any]]) -> dict[str, Any]:
    model = train_decision_model(records)
    status = {key: value for key, value in model.items() if not key.startswith("_")}
    outcome_model = train_outcome_model(records)
    status["measured_outcomes"] = {
        key: value for key, value in outcome_model.items() if not key.startswith("_")
    }
    return status


class DecisionOutcome(BaseModel):
    measured_from: date
    measured_to: date
    actual_spend: float = Field(ge=0)
    actual_revenue: float = Field(ge=0)
    actual_cogs: float = Field(ge=0)
    notes: str = Field(default="", max_length=1000)

    @field_validator("actual_spend", "actual_revenue", "actual_cogs")
    @classmethod
    def validate_finite_amount(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("Financial amounts must be finite numbers.")
        return value


class IntegrationSyncRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=60)


class ExecutionRequest(BaseModel):
    execution_mode: str = Field(default="simulator", pattern="^simulator$")


class LoginRequest(BaseModel):
    email_or_username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


def _normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def _authenticate_request(request: Request) -> User:
    auth_header = request.headers.get("authorization", "")
    token = parse_bearer_token(auth_header)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    async with SessionLocal() as session:
        auth_session = await session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == token_hash,
                AuthSession.revoked_at.is_(None),
            )
        )
        expires_at = _normalize_utc(auth_session.expires_at) if auth_session is not None else None
        if auth_session is None or expires_at is None or expires_at <= datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Your session has expired. Please log in again.")
        user = await session.get(User, auth_session.user_id)
        if user is None or not user.active:
            raise HTTPException(status_code=401, detail="Your session has expired. Please log in again.")
        return user


async def _serialize_user(user: User) -> dict[str, str]:
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "display_name": user.display_name,
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await upgrade_adpilot_schema()
    async with SessionLocal() as session:
        await seed_demo(session)
    redis_url = os.getenv("REDIS_URL", "").strip()
    redis_client = Redis.from_url(redis_url, decode_responses=True) if redis_url else None
    if redis_client is not None:
        try:
            await redis_client.ping()
        except RedisError as exc:
            await redis_client.aclose()
            raise RuntimeError("REDIS_URL is configured but Redis is unavailable.") from exc
    _app.state.redis = redis_client
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_scheduled_simulator_ingestion,
        trigger="interval",
        minutes=int(os.getenv("ADPILOT_INGEST_INTERVAL_MINUTES", "15")),
        id="simulator_incremental_ingestion",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        args=[redis_client],
    )
    if os.getenv("ADPILOT_SCHEDULER_ENABLED", "true").strip().lower() == "true":
        scheduler.start()
        await run_scheduled_simulator_ingestion(redis_client)
    try:
        yield
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        if redis_client is not None:
            await redis_client.aclose()
        await engine.dispose()


app = FastAPI(
    title="AdPilot - D2C Advertising Intelligence Engine",
    description="Backend REST API for cross-channel metrics, anomaly detection, and budget optimization recommendations.",
    version="2.0.0",
    lifespan=lifespan,
)
app.include_router(adpilot_router)


@app.middleware("http")
async def protect_api(request: Request, call_next):
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/auth/"):
        expected_key = os.getenv("PROFITPILOT_API_KEY", "").strip()
        production = os.getenv("APP_ENV", "development").strip().lower() == "production"
        if production and len(expected_key) < 32:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "Set PROFITPILOT_API_KEY to a high-entropy secret of at least 32 "
                        "characters in production."
                    )
                },
            )

        requested_api_key = request.headers.get("x-api-key", "").strip()
        auth_header = request.headers.get("authorization", "")
        token = parse_bearer_token(auth_header)
        if expected_key and requested_api_key == expected_key:
            return await call_next(request)
        if not production and not expected_key:
            return await call_next(request)
        if token:
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            async with SessionLocal() as session:
                auth_session = await session.scalar(
                    select(AuthSession).where(
                        AuthSession.token_hash == token_hash,
                        AuthSession.revoked_at.is_(None),
                    )
                )
                expires_at = _normalize_utc(auth_session.expires_at) if auth_session is not None else None
                if auth_session is not None and expires_at is not None and expires_at > datetime.now(timezone.utc):
                    user = await session.get(User, auth_session.user_id)
                    if user is not None and user.active:
                        request.state.user = user
                        return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required."},
        )
    return await call_next(request)


@app.post("/api/auth/login")
async def login(payload: LoginRequest):
    identifier = payload.email_or_username.strip()
    if not identifier or not payload.password.strip():
        raise HTTPException(status_code=422, detail="Email and password are required.")

    async with SessionLocal() as session:
        users = (await session.scalars(select(User))).all()
        user = next(
            (
                record
                for record in users
                if record.active and (
                    record.email.lower() == identifier.lower() or record.username.lower() == identifier.lower()
                )
            ),
            None,
        )
        if user is None or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        token = new_session_token()
        expires_at = session_expires_at()
        session_record = AuthSession(
            user_id=user.id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=expires_at,
        )
        session.add(session_record)
        await session.commit()
        return {
            "token": token,
            "expires_at": expires_at.isoformat(),
            "user": await _serialize_user(user),
        }


@app.get("/api/auth/me")
async def get_current_user(request: Request):
    user = await _authenticate_request(request)
    return {"user": await _serialize_user(user)}


@app.post("/api/auth/logout")
async def logout(request: Request):
    auth_header = request.headers.get("authorization", "")
    token = parse_bearer_token(auth_header)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    async with SessionLocal() as session:
        auth_session = await session.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == token_hash,
                AuthSession.revoked_at.is_(None),
            )
        )
        if auth_session is not None:
            auth_session.revoked_at = datetime.now(timezone.utc)
            await session.commit()
    return {"detail": "Logged out successfully."}


@app.get("/")
def health_check():
    return {"status": "ok", "message": "ProfitPilot AI API is running."}

@app.get("/api/metrics/summary")
def get_metrics_summary(
    platform: Optional[str] = Query(None, description="Filter by ad platform"),
    sku: Optional[str] = Query(None, description="Filter by SKU ID")
):
    try:
        df = load_data()
        if platform and platform.lower() != "all":
            df = df[df["ad_platform"].str.lower() == platform.lower()]
        if sku and sku.lower() != "all":
            df = df[df["sku_id"].str.lower() == sku.lower()]

        summary_df = campaign_summary(df)
        return summary_df.to_dict(orient="records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/breakdowns")
def get_dimension_breakdowns():
    try:
        df = load_data()
        df["contribution_profit"] = (
            df["sales_revenue"] - df["total_cogs"] - df["ad_spend"]
        )
        result = {}
        for dimension in (
            "ad_platform",
            "channel",
            "product_category",
            "region",
            "customer_segment",
        ):
            if dimension not in df.columns:
                continue
            grouped = df.groupby(dimension, as_index=False, dropna=False).agg(
                spend=("ad_spend", "sum"),
                revenue=("sales_revenue", "sum"),
                contribution_profit=("contribution_profit", "sum"),
                conversions=("conversions", "sum"),
                impressions=("impressions", "sum"),
                clicks=("estimated_clicks", "sum"),
            )
            grouped["roas"] = grouped["revenue"] / grouped["spend"].replace(0, pd.NA)
            grouped["conversion_rate"] = (
                grouped["conversions"] / grouped["clicks"].replace(0, pd.NA)
            )
            grouped = grouped.astype(object).where(pd.notna(grouped), None)
            result[dimension] = grouped.to_dict(orient="records")
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/anomalies")
def get_anomalies(
    warning_threshold: float = -0.20,
    critical_threshold: float = -0.40,
    limit: int = Query(150, ge=1, le=500),
):
    try:
        df = load_data()
        daily_anomalies = detect_anomalies(
            df, 
            warning_threshold=warning_threshold, 
            critical_threshold=critical_threshold
        )
        flagged = daily_anomalies[daily_anomalies["is_anomaly"]].copy()
        flagged = flagged.sort_values("anomaly_risk_score", ascending=False).head(limit)
        return [build_ai_alert_payload(row) for _, row in flagged.iterrows()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/anomaly-forecast")
def get_anomaly_forecast():
    try:
        df = load_data()
        forecast = forecast_campaign_anomalies(df)
        forecast["dataset_period"] = {
            "start": df["date"].min().date().isoformat(),
            "end": df["date"].max().date().isoformat(),
        }
        forecast["campaign_count"] = int(df["campaign_id"].nunique())
        return forecast
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/data-profile")
def get_data_profile():
    try:
        df = load_data()
        dimensions = [
            "ad_platform",
            "channel",
            "region",
            "product_category",
            "customer_segment",
            "sku_id",
            "campaign_id",
        ]
        return {
            "rows": len(df),
            "date_start": df["date"].min().date().isoformat(),
            "date_end": df["date"].max().date().isoformat(),
            "dimensions": {
                column: int(df[column].nunique())
                for column in dimensions
                if column in df.columns
            },
            "creative_data_available": any(
                column in df.columns
                for column in ("creative_id", "creative_name", "creative_asset")
            ),
            "data_source": os.getenv("PROFITPILOT_DATA_SOURCE", "sample").lower(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/integrations/status")
def get_integrations_status():
    try:
        return integration_status()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/integrations/shopify/orders")
def get_shopify_order_summary():
    try:
        orders = load_shopify_orders_snapshot()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not read Shopify order data: {exc}") from exc
    if orders.empty:
        return {
            "records": [],
            "currency_code": None,
            "sales_revenue": 0.0,
            "units_sold": 0,
            "total_orders": 0,
        }
    by_sku = (
        orders.groupby("sku_id", as_index=False)
        .agg(
            order_sku_count=("order_count", "sum"),
            units_sold=("units_sold", "sum"),
            sales_revenue=("sales_revenue", "sum"),
        )
        .sort_values("sales_revenue", ascending=False)
    )
    return {
        "records": by_sku.to_dict(orient="records"),
        "currency_code": str(orders["currency_code"].iloc[0]),
        "sales_revenue": round(float(orders["sales_revenue"].sum()), 2),
        "units_sold": int(orders["units_sold"].sum()),
        "order_sku_count": int(orders["order_count"].sum()),
        "total_orders": int(
            (integration_status().get("last_sync") or {}).get("shopify_order_count", 0)
        ),
    }


@app.post("/api/integrations/sync")
def sync_live_integrations(request: IntegrationSyncRequest):
    try:
        if not integration_status()["ready_to_sync"]:
            raise IntegrationConfigurationError(
                "Configure Shopify, Google Ads, and an explicit campaign-to-SKU mapping before syncing."
            )
        return {
            "status": "synced",
            "snapshot": sync_integrations(request.days),
            "integrations": integration_status(),
        }
    except IntegrationConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrationRequestError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/recommendations")
def get_recommendations(
    platform: Optional[str] = Query(None),
    priority: Optional[str] = Query(None)
):
    try:
        df = load_data()
        summary = campaign_summary(df)
        recs = generate_recommendations(summary)

        if platform and platform.lower() != "all":
            recs = recs[recs["ad_platform"].str.lower() == platform.lower()]
        if priority and priority.lower() != "all":
            recs = recs[recs["decision_priority"].str.lower() == priority.lower()]

        decisions = _read_decisions()
        model = train_decision_model(decisions)
        outcome_model = train_outcome_model(decisions)
        payloads = [build_recommendation_payload(row) for _, row in recs.iterrows()]
        for payload in payloads:
            prediction = predict_decision(model, payload)
            if prediction is not None:
                payload["human_feedback_prediction"] = prediction
            profitability_prediction = predict_decision(outcome_model, payload)
            if profitability_prediction is not None:
                payload["profitability_outcome_prediction"] = profitability_prediction
        return {
            "portfolio_summary": portfolio_summary(recs),
            "recommendations": payloads,
            "learning_status": _public_learning_status(decisions),
        }
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/decisions")
def list_decisions():
    try:
        return _read_decisions()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/learning/status")
def get_learning_status():
    try:
        return _public_learning_status(_read_decisions())
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/decisions")
def save_decision(record: Dict[str, Any]):
    required_fields = [
        "timestamp",
        "proposed_action",
        "budget_change_pct",
        "human_decision",
        "decision_source",
    ]
    missing = [field for field in required_fields if field not in record]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required fields: {', '.join(missing)}")
    if record["human_decision"] not in ("Approve", "Reject", "Monitor"):
        raise HTTPException(status_code=422, detail="human_decision must be Approve, Reject, or Monitor.")
    if "model_features" in record and not isinstance(record["model_features"], dict):
        raise HTTPException(status_code=422, detail="model_features must be a JSON object.")
    record.setdefault("decision_id", str(uuid4()))

    try:
        with DECISIONS_LOCK:
            decisions = _read_decisions()
            decisions.append(record)
            _write_decisions(decisions)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {
        "status": "saved",
        "decision": record,
        "total": len(decisions),
        "learning_status": _public_learning_status(decisions),
    }


@app.post("/api/decisions/{decision_id}/outcome")
def save_decision_outcome(decision_id: str, outcome: DecisionOutcome):
    if outcome.measured_to < outcome.measured_from:
        raise HTTPException(status_code=422, detail="measured_to must not be earlier than measured_from.")
    if outcome.measured_to > date.today():
        raise HTTPException(status_code=422, detail="measured_to cannot be in the future.")
    if outcome.actual_spend == 0 and outcome.actual_revenue == 0:
        raise HTTPException(
            status_code=422,
            detail="Enter measured spend or revenue before saving an outcome.",
        )

    try:
        with DECISIONS_LOCK:
            decisions = _read_decisions()
            record = next(
                (
                    item for item in decisions
                    if item.get("decision_id") == decision_id
                ),
                None,
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Decision record was not found.")
            if record.get("outcome"):
                raise HTTPException(status_code=409, detail="An outcome is already recorded for this decision.")

            contribution_profit = (
                outcome.actual_revenue - outcome.actual_cogs - outcome.actual_spend
            )
            record["outcome"] = {
                "measured_from": outcome.measured_from.isoformat(),
                "measured_to": outcome.measured_to.isoformat(),
                "actual_spend": outcome.actual_spend,
                "actual_revenue": outcome.actual_revenue,
                "actual_cogs": outcome.actual_cogs,
                "actual_contribution_profit": round(contribution_profit, 2),
                "profitability_label": (
                    "Profitable" if contribution_profit > 0 else "Unprofitable"
                ),
                "notes": outcome.notes.strip(),
            }
            _write_decisions(decisions)
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "status": "outcome_saved",
        "decision": record,
        "learning_status": _public_learning_status(decisions),
    }


@app.post("/api/decisions/{decision_id}/execute")
def execute_decision(decision_id: str, request: ExecutionRequest = ExecutionRequest()):
    try:
        with DECISIONS_LOCK:
            decisions = _read_decisions()
            record = next(
                (item for item in decisions if item.get("decision_id") == decision_id),
                None,
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Decision record was not found.")
            if record.get("human_decision") != "Approve":
                raise HTTPException(status_code=409, detail="Only approved decisions can be executed.")
            if record.get("execution"):
                raise HTTPException(status_code=409, detail="This decision already has an execution record.")
            if record.get("proposed_action") not in {
                "Increase budget", "Reduce budget", "Hold budget", "Maintain budget"
            }:
                raise HTTPException(status_code=422, detail="The proposed action is not supported by simulator mode.")

            execution = {
                "execution_id": str(uuid4()),
                "mode": request.execution_mode,
                "status": "simulated",
                "action": record["proposed_action"],
                "budget_change_pct": record.get("budget_change_pct", 0),
                "executed_at": pd.Timestamp.now(tz="UTC").isoformat(),
                "rollback_available": True,
            }
            record["execution"] = execution
            _write_decisions(decisions)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"status": "simulated", "decision": record, "execution": execution}


@app.post("/api/decisions/{decision_id}/rollback")
def rollback_decision(decision_id: str):
    try:
        with DECISIONS_LOCK:
            decisions = _read_decisions()
            record = next(
                (item for item in decisions if item.get("decision_id") == decision_id),
                None,
            )
            if record is None:
                raise HTTPException(status_code=404, detail="Decision record was not found.")
            execution = record.get("execution")
            if not execution or execution.get("status") != "simulated":
                raise HTTPException(status_code=409, detail="No active simulator execution is available to roll back.")

            execution["status"] = "rolled_back"
            execution["rolled_back_at"] = pd.Timestamp.now(tz="UTC").isoformat()
            execution["rollback_available"] = False
            _write_decisions(decisions)
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"status": "rolled_back", "decision": record, "execution": execution}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)