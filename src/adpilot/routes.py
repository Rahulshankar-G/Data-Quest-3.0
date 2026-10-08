import csv
import io
import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from redis.exceptions import RedisError

from src.adpilot.analysis import (
    changepoint_indices,
    decompose_roas,
    fit_response_curve,
    optimize_budget,
    robust_anomaly_scores,
    stl_residual_scores,
)
from src.adpilot.connectors import CONNECTORS, SCENARIO_CATALOG
from src.adpilot.db import get_session
from src.adpilot.jobs import ingest_simulator_data
from src.adpilot.models import (
    AdAccount,
    AdMetricDaily,
    AgentStep,
    Anomaly,
    Brand,
    Campaign,
    CampaignSkuMap,
    Creative,
    Diagnosis,
    Execution,
    InventorySnapshot,
    ModelRegistry,
    Opportunity,
    Order,
    Platform,
    Product,
    Recommendation,
    Outcome,
    SimulationState,
    UnifiedFact,
    new_id,
)
from src.adpilot.seed import BRAND_NAME

router = APIRouter(prefix="/api/v1", tags=["AdPilot"])


async def _brand(session: AsyncSession) -> Brand:
    brand = await session.scalar(select(Brand).where(Brand.name == BRAND_NAME))
    if brand is None:
        raise HTTPException(status_code=503, detail="AdPilot demo brand is not initialized.")
    return brand


def _iso(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _recommendation_dict(item: Recommendation) -> dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "rationale": item.rationale,
        "scenario_key": item.scenario_key,
        "campaign_id": item.campaign_id,
        "proposed_change_pct": item.proposed_change_pct,
        "priority_score": item.priority_score,
        "status": item.status,
        "constraints": item.constraints,
        "payload": item.payload,
        "created_at": _iso(item.created_at),
    }


class ApprovalInput(BaseModel):
    approved_by: str = Field(min_length=2, max_length=160)


class ApprovalRevocationInput(BaseModel):
    revoked_by: str = Field(min_length=2, max_length=160)


class ClockAdvanceInput(BaseModel):
    days: int = Field(ge=1, le=30)


class ChatInput(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    scenario_key: str | None = None


class OptimizerInput(BaseModel):
    total_budget: float = Field(gt=0, le=1_000_000)
    max_shift_pct: float = Field(ge=0, le=50, default=20)
    target_roas: float | None = Field(default=None, ge=0, le=100)


@router.get("/health")
async def health(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await session.execute(select(1))
    redis_client = getattr(request.app.state, "redis", None)
    redis_status = "not_configured"
    if redis_client is not None:
        try:
            await redis_client.ping()
            redis_status = "connected"
        except RedisError:
            redis_status = "unavailable"
    return {
        "status": "degraded" if redis_status == "unavailable" else "ok",
        "database": "connected",
        "redis": redis_status,
        "application": "adpilot",
    }


@router.get("/dashboard")
async def dashboard(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    facts = list(
        (
            await session.scalars(
                select(UnifiedFact).where(UnifiedFact.brand_id == brand.id)
            )
        ).all()
    )
    recommendations = list(
        (
            await session.scalars(
                select(Recommendation)
                .where(Recommendation.brand_id == brand.id)
                .order_by(Recommendation.priority_score.desc())
                .limit(20)
            )
        ).all()
    )
    anomalies = list(
        (
            await session.scalars(
                select(Anomaly)
                .where(Anomaly.brand_id == brand.id)
                .order_by(Anomaly.score.desc())
                .limit(12)
            )
        ).all()
    )
    sim_clock = await session.scalar(
        select(SimulationState).where(SimulationState.brand_id == brand.id)
    )
    current_facts = [
        fact for fact in facts if fact.fact_date >= date.today() - timedelta(days=6)
    ]
    spend = sum(fact.spend for fact in current_facts)
    revenue = sum(fact.reconciled_revenue for fact in current_facts)
    profit = sum(fact.contribution_profit for fact in current_facts)
    return {
        "brand": brand.name,
        "window": "last 7 simulated days",
        "kpis": {
            "revenue": round(revenue, 2),
            "spend": round(spend, 2),
            "contribution_profit": round(profit, 2),
            "roas": round(revenue / spend, 3) if spend else 0,
            "cumulative_ai_profit_uplift": round(
                sim_clock.cumulative_profit_uplift if sim_clock else 0,
                2,
            ),
        },
        "pipeline": {
            "ingest": "healthy",
            "reconcile": "healthy",
            "diagnose": "healthy",
            "decide": "healthy",
            "execute": "simulator_only",
            "learn": "active",
        },
        "what_changed": [
            {
                "id": item.id,
                "title": item.evidence.get("title", item.scenario_key),
                "severity": item.severity,
                "summary": item.evidence.get("summary", ""),
                "scenario_key": item.scenario_key,
            }
            for item in anomalies
        ],
        "recommendations": [_recommendation_dict(item) for item in recommendations],
        "simulation_clock": {
            "date": _iso(sim_clock.simulated_date) if sim_clock else date.today().isoformat(),
            "advanced_days": sim_clock.advanced_days if sim_clock else 0,
        },
    }


@router.get("/scenarios")
async def scenarios(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    anomalies = list(
        (
            await session.scalars(
                select(Anomaly).where(Anomaly.brand_id == brand.id)
            )
        ).all()
    )
    recommendations = list(
        (
            await session.scalars(
                select(Recommendation).where(Recommendation.brand_id == brand.id)
            )
        ).all()
    )
    anomalies_by_key = {item.scenario_key: item for item in anomalies}
    recommendations_by_key = {item.scenario_key: item for item in recommendations}
    return [
        {
            **scenario,
            "anomaly": {
                "id": anomalies_by_key[scenario["key"]].id,
                "severity": anomalies_by_key[scenario["key"]].severity,
                "score": anomalies_by_key[scenario["key"]].score,
                "evidence": anomalies_by_key[scenario["key"]].evidence,
            }
            if scenario["key"] in anomalies_by_key else None,
            "recommendation": _recommendation_dict(recommendations_by_key[scenario["key"]])
            if scenario["key"] in recommendations_by_key else None,
        }
        for scenario in SCENARIO_CATALOG
    ]


@router.get("/lineage")
async def lineage(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    platforms = list((await session.scalars(select(Platform))).all())
    products = list(
        (await session.scalars(select(Product).where(Product.brand_id == brand.id))).all()
    )
    campaigns = list(
        (await session.scalars(select(Campaign).where(Campaign.brand_id == brand.id))).all()
    )
    return {
        "nodes": (
            [{"id": f"source-{row.key}", "label": row.name, "kind": "source"} for row in platforms]
            + [{"id": "reconciliation", "label": "Proportional attribution reconciliation", "kind": "process"}]
            + [{"id": "unified-facts", "label": "Contribution-profit facts", "kind": "dataset"}]
            + [{"id": f"sku-{row.id}", "label": f"{row.name} · {row.sku}", "kind": "product"} for row in products]
            + [{"id": "anomaly-engine", "label": "Anomaly & diagnosis models", "kind": "model"}]
            + [{"id": "decision-engine", "label": "Budget decision optimizer", "kind": "model"}]
            + [{"id": "execution", "label": "Simulator execution", "kind": "execution"}]
            + [{"id": "outcomes", "label": "Measured outcomes", "kind": "dataset"}]
        ),
        "edges": (
            [{"id": f"{row.key}-reconcile", "source": f"source-{row.key}", "target": "reconciliation"} for row in platforms]
            + [{"id": "reconcile-facts", "source": "reconciliation", "target": "unified-facts"}]
            + [{"id": f"fact-sku-{row.id}", "source": "unified-facts", "target": f"sku-{row.id}"} for row in products]
            + [{"id": f"fact-campaign-{row.id}", "source": "unified-facts", "target": "anomaly-engine"} for row in campaigns[:8]]
            + [{"id": "diagnose-decide", "source": "anomaly-engine", "target": "decision-engine"}]
            + [{"id": "decide-execute", "source": "decision-engine", "target": "execution"}]
            + [{"id": "execute-learn", "source": "execution", "target": "outcomes"}]
            + [{"id": "learn-loop", "source": "outcomes", "target": "anomaly-engine"}]
        ),
    }


@router.get("/performance")
async def performance(
    platform: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    brand = await _brand(session)
    statement = select(UnifiedFact).where(UnifiedFact.brand_id == brand.id)
    if platform and platform != "all":
        statement = statement.where(UnifiedFact.platform == platform)
    facts = list((await session.scalars(statement.order_by(UnifiedFact.fact_date))).all())
    products = {
        item.id: item
        for item in (
            await session.scalars(select(Product).where(Product.brand_id == brand.id))
        ).all()
    }
    campaigns = {
        item.id: item
        for item in (
            await session.scalars(select(Campaign).where(Campaign.brand_id == brand.id))
        ).all()
    }
    groups: dict[str, dict[str, Any]] = {}
    for fact in facts:
        product = products.get(fact.product_id)
        campaign = campaigns.get(fact.campaign_id)
        key = f"{fact.platform}|{campaign.id if campaign else ''}|{product.sku if product else ''}"
        group = groups.setdefault(key, {
            "platform": fact.platform,
            "campaign_id": campaign.id if campaign else None,
            "campaign": campaign.name if campaign else "Unmapped",
            "daily_budget": float(campaign.daily_budget) if campaign else 0.0,
            "sku": product.sku if product else None,
            "spend": 0.0,
            "revenue": 0.0,
            "attributed_revenue": 0.0,
            "profit": 0.0,
            "clicks": 0,
            "impressions": 0,
            "conversions": 0.0,
            "inventory_cover_days": None,
            "days": {},
        })
        group["spend"] += fact.spend
        group["revenue"] += fact.reconciled_revenue
        group["attributed_revenue"] += fact.attributed_revenue
        group["profit"] += fact.contribution_profit
        group["clicks"] += fact.clicks
        group["impressions"] += fact.impressions
        group["conversions"] += fact.conversions
        day = group["days"].setdefault(fact.fact_date.isoformat(), {
            "date": fact.fact_date.isoformat(),
            "spend": 0,
            "revenue": 0,
            "profit": 0,
            "clicks": 0,
            "impressions": 0,
            "conversions": 0,
        })
        day["spend"] += fact.spend
        day["revenue"] += fact.reconciled_revenue
        day["profit"] += fact.contribution_profit
        day["clicks"] += fact.clicks
        day["impressions"] += fact.impressions
        day["conversions"] += fact.conversions
    product_ids = list(products)
    inventory = list(
        (
            await session.scalars(
                select(InventorySnapshot)
                .where(InventorySnapshot.product_id.in_(product_ids))
                .order_by(InventorySnapshot.captured_at.desc())
            )
        ).all()
    ) if product_ids else []
    inventory_by_product = {}
    for snapshot in inventory:
        inventory_by_product.setdefault(snapshot.product_id, snapshot)
    mapping = {
        item.campaign_id: item.product_id
        for item in (
            await session.scalars(
                select(CampaignSkuMap).where(CampaignSkuMap.product_id.in_(product_ids))
            )
        ).all()
    } if product_ids else {}
    for group in groups.values():
        campaign_id = group["campaign_id"]
        product_id = mapping.get(campaign_id)
        snapshot = inventory_by_product.get(product_id)
        if snapshot and snapshot.units_per_day:
            group["inventory_cover_days"] = snapshot.on_hand / snapshot.units_per_day
        group["roas"] = group["revenue"] / group["spend"] if group["spend"] else 0
        group["ctr"] = group["clicks"] / group["impressions"] if group["impressions"] else 0
        group["cvr"] = group["conversions"] / group["clicks"] if group["clicks"] else 0
        group["aov"] = group["revenue"] / group["conversions"] if group["conversions"] else 0
        group["cpc"] = group["spend"] / group["clicks"] if group["clicks"] else 0
        group["days"] = sorted(group["days"].values(), key=lambda row: row["date"])
    return {"rows": list(groups.values()), "count": len(groups)}


@router.get("/diagnostics")
async def diagnostics(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    anomalies = list(
        (
            await session.scalars(
                select(Anomaly)
                .where(Anomaly.brand_id == brand.id)
                .order_by(Anomaly.score.desc())
            )
        ).all()
    )
    diagnoses = list((await session.scalars(select(Diagnosis))).all())
    diagnosis_by_anomaly = {item.anomaly_id: item for item in diagnoses}
    agent_steps = list(
        (
            await session.scalars(
                select(AgentStep)
                .where(AgentStep.brand_id == brand.id)
                .order_by(AgentStep.created_at.desc(), AgentStep.step_index)
                .limit(120)
            )
        ).all()
    )
    return {
        "anomalies": [
            {
                "id": anomaly.id,
                "scenario_key": anomaly.scenario_key,
                "severity": anomaly.severity,
                "metric": anomaly.metric,
                "score": anomaly.score,
                "status": anomaly.status,
                "evidence": anomaly.evidence,
                "diagnosis": {
                    "summary": diagnosis_by_anomaly[anomaly.id].summary,
                    "drivers": diagnosis_by_anomaly[anomaly.id].drivers,
                    "decomposition": diagnosis_by_anomaly[anomaly.id].decomposition,
                    "confidence": diagnosis_by_anomaly[anomaly.id].confidence,
                } if anomaly.id in diagnosis_by_anomaly else None,
            }
            for anomaly in anomalies
        ],
        "agent_steps": [
            {
                "id": item.id,
                "run_id": item.run_id,
                "step_index": item.step_index,
                "tool_name": item.tool_name,
                "input": item.input,
                "output": item.output,
                "created_at": _iso(item.created_at),
            }
            for item in agent_steps
        ],
    }


@router.post("/diagnostics/{scenario_key}/run")
async def run_diagnosis(
    scenario_key: str,
    session: AsyncSession = Depends(get_session),
):
    brand = await _brand(session)
    anomaly = await session.scalar(
        select(Anomaly).where(
            Anomaly.brand_id == brand.id,
            Anomaly.scenario_key == scenario_key,
        )
    )
    if anomaly is None:
        raise HTTPException(status_code=404, detail="Scenario anomaly was not found.")
    evidence = anomaly.evidence
    campaign_id = evidence.get("campaign_id")
    facts = list(
        (
            await session.scalars(
                select(UnifiedFact)
                .where(
                    UnifiedFact.brand_id == brand.id,
                    UnifiedFact.campaign_id == campaign_id,
                )
                .order_by(UnifiedFact.fact_date.desc())
                .limit(30)
            )
        ).all()
    )
    facts.reverse()
    latest = facts[-1] if facts else None
    baseline_rows = facts[-8:-1] if len(facts) > 1 else []
    current_values = {
        "ctr": latest.clicks / max(latest.impressions, 1) if latest else 0,
        "cvr": latest.conversions / max(latest.clicks, 1) if latest else 0,
        "aov": latest.reconciled_revenue / max(latest.conversions, 1) if latest else 0,
        "cpc": latest.spend / max(latest.clicks, 1) if latest else 0,
    }
    previous_values = {
        "ctr": sum(row.clicks for row in baseline_rows) / max(sum(row.impressions for row in baseline_rows), 1),
        "cvr": sum(row.conversions for row in baseline_rows) / max(sum(row.clicks for row in baseline_rows), 1),
        "aov": sum(row.reconciled_revenue for row in baseline_rows) / max(sum(row.conversions for row in baseline_rows), 1),
        "cpc": sum(row.spend for row in baseline_rows) / max(sum(row.clicks for row in baseline_rows), 1),
    }
    decomposition = decompose_roas(current_values, previous_values)
    statistical_scores = {
        "robust_z_mad": robust_anomaly_scores(
            [float(row.contribution_profit) for row in facts]
        ),
        "stl_residual": stl_residual_scores(
            [float(row.contribution_profit) for row in facts]
        ),
        "pelt_changepoints": changepoint_indices(
            [float(row.contribution_profit) for row in facts]
        ),
    }
    product_mapping = await session.scalar(
        select(CampaignSkuMap).where(CampaignSkuMap.campaign_id == campaign_id)
    )
    inventory = await session.scalar(
        select(InventorySnapshot)
        .where(InventorySnapshot.product_id == product_mapping.product_id)
        .order_by(InventorySnapshot.captured_at.desc())
        .limit(1)
    ) if product_mapping else None
    latest_metric = await session.scalar(
        select(AdMetricDaily)
        .where(AdMetricDaily.campaign_id == campaign_id)
        .order_by(AdMetricDaily.metric_date.desc())
        .limit(1)
    )
    metric_payload = latest_metric.source_payload if latest_metric else {}
    evidence = {
        **evidence,
        "latest_metrics": {
            "date": latest.fact_date.isoformat() if latest else None,
            "spend": round(float(latest.spend), 2) if latest else None,
            "reconciled_revenue": round(float(latest.reconciled_revenue), 2) if latest else None,
            "contribution_profit": round(float(latest.contribution_profit), 2) if latest else None,
        },
        "ctr": current_values["ctr"],
        "cvr": current_values["cvr"],
        "aov": current_values["aov"],
        "cpc": current_values["cpc"],
        "previous_ctr": previous_values["ctr"],
        "previous_cvr": previous_values["cvr"],
        "previous_aov": previous_values["aov"],
        "previous_cpc": previous_values["cpc"],
        "inventory": inventory.on_hand if inventory else evidence.get("inventory"),
        "inventory_cover_days": (
            inventory.on_hand / inventory.units_per_day
            if inventory and inventory.units_per_day
            else evidence.get("inventory_cover_days")
        ),
        "frequency": metric_payload.get("frequency", evidence.get("frequency")),
        "ga4_events": metric_payload.get(
            "ga4_purchase_events",
            evidence.get("ga4_events"),
        ),
        "reconciliation_factor": latest.reconciliation_factor if latest else evidence.get("reconciliation_factor"),
        "statistical_scores": {
            "robust_z_mad": statistical_scores["robust_z_mad"][-1]
            if statistical_scores["robust_z_mad"] else 0,
            "stl_residual": statistical_scores["stl_residual"][-1]
            if statistical_scores["stl_residual"] else 0,
            "pelt_changepoints": statistical_scores["pelt_changepoints"],
        },
    }
    run_id = str(uuid4())
    tool_results = [
        (
            "query_unified_facts",
            {"scenario_key": scenario_key},
            {
                "platform": evidence.get("platform"),
                "campaign_id": evidence.get("campaign_id"),
                "spend": evidence.get("spend"),
                "attributed_revenue": evidence.get("revenue"),
            },
        ),
        (
            "run_decomposition",
            {"formula": "ROAS = CTR × CVR × AOV / CPC"},
            decomposition,
        ),
        (
            "check_inventory",
            {"sku": evidence.get("sku")},
            {
                "on_hand": evidence.get("inventory"),
                "units_per_day": evidence.get("units_per_day"),
                "cover_days": evidence.get("inventory_cover_days"),
                "guardrail_days": 5,
            },
        ),
        (
            "check_creative_fatigue",
            {"campaign_id": evidence.get("campaign_id")},
            {"frequency": evidence.get("frequency"), "drivers": evidence.get("drivers", [])},
        ),
        (
            "check_tracking_health",
            {"scenario_key": scenario_key},
            {
                "ga4_purchase_events": evidence.get("ga4_events"),
                "store_orders_stable": scenario_key == "ga4_tracking_outage",
                "attribution_factor": evidence.get("reconciliation_factor"),
            },
        ),
    ]
    for index, (tool, input_data, output_data) in enumerate(tool_results, start=1):
        session.add(
            AgentStep(
                brand_id=brand.id,
                run_id=run_id,
                step_index=index,
                tool_name=tool,
                input=input_data,
                output=output_data,
            )
        )
    diagnosis = await session.scalar(
        select(Diagnosis).where(Diagnosis.anomaly_id == anomaly.id)
    )
    if diagnosis is None:
        diagnosis = Diagnosis(anomaly_id=anomaly.id, summary=evidence.get("summary", ""))
        session.add(diagnosis)
    diagnosis.decomposition = tool_results[1][2]
    diagnosis.drivers = evidence.get("drivers", [])
    diagnosis.summary = evidence.get("summary", diagnosis.summary)
    anomaly.evidence = evidence
    await session.commit()
    return {
        "run_id": run_id,
        "summary": evidence.get("summary"),
        "drivers": evidence.get("drivers", []),
        "decomposition": tool_results[1][2],
        "statistical_scores": evidence["statistical_scores"],
        "steps": [
            {"step": index, "tool": tool, "input": input_data, "output": output_data}
            for index, (tool, input_data, output_data) in enumerate(tool_results, start=1)
        ],
    }


@router.post("/chat")
async def reasoning_chat(
    payload: ChatInput,
    session: AsyncSession = Depends(get_session),
):
    brand = await _brand(session)
    statement = select(Anomaly).where(Anomaly.brand_id == brand.id)
    if payload.scenario_key:
        statement = statement.where(Anomaly.scenario_key == payload.scenario_key)
    anomalies = list((await session.scalars(statement)).all())
    question = payload.question.casefold()
    matching = next(
        (
            item for item in anomalies
            if item.scenario_key and any(token in question for token in item.scenario_key.replace("_", " ").split())
        ),
        anomalies[0] if anomalies else None,
    )
    evidence = matching.evidence if matching else {}
    from src.adpilot.explanations import explain
    explanation = await explain(payload.question, evidence)
    return {
        **explanation,
        "evidence": evidence,
    }


@router.get("/opportunities")
async def opportunities(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    rows = list(
        (
            await session.scalars(
                select(Opportunity)
                .where(Opportunity.brand_id == brand.id)
                .order_by(Opportunity.confidence.desc())
            )
        ).all()
    )
    return [
        {
            "id": row.id,
            "scenario_key": row.scenario_key,
            "title": row.title,
            "kind": row.kind,
            "estimated_profit_uplift": row.estimated_profit_uplift,
            "confidence": row.confidence,
            "features": row.features,
            "status": row.status,
        }
        for row in rows
    ]


@router.get("/decisions")
async def decisions(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    recs = list(
        (
            await session.scalars(
                select(Recommendation)
                .where(Recommendation.brand_id == brand.id)
                .order_by(Recommendation.priority_score.desc())
            )
        ).all()
    )
    executions = list((await session.scalars(select(Execution))).all())
    execution_by_rec = {item.recommendation_id: item for item in executions}
    return [
        {
            **_recommendation_dict(rec),
            "execution": {
                "id": execution_by_rec[rec.id].id,
                "mode": execution_by_rec[rec.id].mode,
                "status": execution_by_rec[rec.id].status,
                "before_state": execution_by_rec[rec.id].before_state,
                "after_state": execution_by_rec[rec.id].after_state,
                "executed_at": _iso(execution_by_rec[rec.id].executed_at),
                "rolled_back_at": _iso(execution_by_rec[rec.id].rolled_back_at),
            } if rec.id in execution_by_rec else None,
        }
        for rec in recs
    ]


@router.post("/recommendations/{recommendation_id}/approve")
async def approve_recommendation(
    recommendation_id: str,
    payload: ApprovalInput,
    session: AsyncSession = Depends(get_session),
):
    brand = await _brand(session)
    recommendation = await session.scalar(
        select(Recommendation).where(
            Recommendation.id == recommendation_id,
            Recommendation.brand_id == brand.id,
        )
    )
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Recommendation was not found.")
    if recommendation.status != "proposed":
        raise HTTPException(status_code=409, detail="Only proposed recommendations can be approved.")
    recommendation.status = "approved"
    recommendation.payload = {
        **recommendation.payload,
        "approved_by": payload.approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    await session.commit()
    return _recommendation_dict(recommendation)


@router.post("/recommendations/{recommendation_id}/revoke-approval")
async def revoke_recommendation_approval(
    recommendation_id: str,
    payload: ApprovalRevocationInput,
    session: AsyncSession = Depends(get_session),
):
    brand = await _brand(session)
    recommendation = await session.scalar(
        select(Recommendation).where(
            Recommendation.id == recommendation_id,
            Recommendation.brand_id == brand.id,
        )
    )
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Recommendation was not found.")
    if recommendation.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Only approved, unexecuted recommendations can have approval revoked.",
        )

    recommendation.status = "proposed"
    recommendation.payload = {
        **{
            key: value
            for key, value in recommendation.payload.items()
            if key not in {"approved_by", "approved_at"}
        },
        "approval_revoked_by": payload.revoked_by,
        "approval_revoked_at": datetime.now(timezone.utc).isoformat(),
    }
    await session.commit()
    return _recommendation_dict(recommendation)


@router.post("/recommendations/{recommendation_id}/execute")
async def execute_recommendation(
    recommendation_id: str,
    session: AsyncSession = Depends(get_session),
):
    brand = await _brand(session)
    recommendation = await session.scalar(
        select(Recommendation).where(
            Recommendation.id == recommendation_id,
            Recommendation.brand_id == brand.id,
        )
    )
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Recommendation was not found.")
    if recommendation.status != "approved":
        raise HTTPException(status_code=409, detail="Approve the recommendation before execution.")
    campaign = await session.get(Campaign, recommendation.campaign_id)
    if campaign is None:
        raise HTTPException(status_code=409, detail="The recommendation has no active campaign.")
    inventory_days = recommendation.constraints.get("inventory_cover_days")
    if inventory_days is not None and inventory_days < 5 and recommendation.proposed_change_pct > 0:
        raise HTTPException(status_code=409, detail="Inventory cover is below five days; scaling is blocked.")
    platform = await session.get(Platform, campaign.platform_id)
    connector = CONNECTORS.get(platform.key) if platform else None
    if connector is None:
        raise HTTPException(status_code=409, detail="No simulator connector is registered for this platform.")
    before_budget = float(campaign.daily_budget)
    after_budget = max(0, before_budget * (1 + recommendation.proposed_change_pct / 100))
    provider_result = await connector.push_changes(
        campaign.external_id,
        {"daily_budget": round(after_budget, 2)},
    )
    execution = Execution(
        recommendation_id=recommendation.id,
        mode="simulator",
        status="executed",
        before_state={"daily_budget": before_budget},
        after_state={"daily_budget": round(after_budget, 2), "provider": provider_result},
    )
    campaign.daily_budget = round(after_budget, 2)
    recommendation.status = "executed"
    session.add(execution)
    await session.commit()
    return {
        "status": "executed_in_simulator",
        "execution": {
            "id": execution.id,
            "mode": execution.mode,
            "status": execution.status,
            "before_state": execution.before_state,
            "after_state": execution.after_state,
        },
        "recommendation": _recommendation_dict(recommendation),
    }


@router.post("/executions/{execution_id}/rollback")
async def rollback_execution(
    execution_id: str,
    session: AsyncSession = Depends(get_session),
):
    execution = await session.get(Execution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution was not found.")
    if execution.status != "executed" or execution.rolled_back_at is not None:
        raise HTTPException(status_code=409, detail="This execution is not eligible for rollback.")
    recommendation = await session.get(Recommendation, execution.recommendation_id)
    campaign = await session.get(Campaign, recommendation.campaign_id) if recommendation else None
    if campaign is None:
        raise HTTPException(status_code=409, detail="The executed campaign no longer exists.")
    campaign.daily_budget = float(execution.before_state["daily_budget"])
    execution.status = "rolled_back"
    execution.rolled_back_at = datetime.now(timezone.utc)
    recommendation.status = "approved"
    await session.commit()
    return {"status": "rolled_back", "daily_budget": campaign.daily_budget, "execution_id": execution.id}


@router.post("/optimizer")
async def budget_optimizer(
    payload: OptimizerInput,
    session: AsyncSession = Depends(get_session),
):
    brand = await _brand(session)
    campaigns = list(
        (
            await session.scalars(
                select(Campaign).where(Campaign.brand_id == brand.id)
            )
        ).all()
    )
    latest_fact_date = await session.scalar(
        select(func.max(UnifiedFact.fact_date)).where(
            UnifiedFact.brand_id == brand.id,
            UnifiedFact.campaign_id.is_not(None),
            UnifiedFact.source != "simulator_clock",
        )
    )
    if latest_fact_date is None:
        result = optimize_budget(
            [],
            payload.total_budget,
            payload.max_shift_pct,
            payload.target_roas,
        )
        result["response_curves"] = {}
        result["data_window"] = {"start": None, "end": None, "days": 0}
        result["budget_period"] = "daily"
        return result
    window_start = latest_fact_date - timedelta(days=29)
    daily_rows = list(
        (
            await session.execute(
                select(
                    UnifiedFact.campaign_id,
                    UnifiedFact.fact_date,
                    func.sum(UnifiedFact.spend),
                    func.sum(UnifiedFact.reconciled_revenue),
                    func.sum(UnifiedFact.cogs),
                    func.sum(UnifiedFact.contribution_profit),
                )
                .where(
                    UnifiedFact.brand_id == brand.id,
                    UnifiedFact.campaign_id.is_not(None),
                    UnifiedFact.source != "simulator_clock",
                    UnifiedFact.fact_date >= window_start,
                    UnifiedFact.fact_date <= latest_fact_date,
                )
                .group_by(UnifiedFact.campaign_id, UnifiedFact.fact_date)
                .order_by(UnifiedFact.campaign_id, UnifiedFact.fact_date)
            )
        ).all()
    )
    facts_by_campaign: dict[str, list[dict[str, float]]] = {}
    for campaign_id, _, spend, revenue, cogs, profit in daily_rows:
        facts_by_campaign.setdefault(campaign_id, []).append({
            "spend": float(spend or 0),
            "revenue": float(revenue or 0),
            "cogs": float(cogs or 0),
            "profit": float(profit or 0),
        })
    campaign_by_id = {campaign.id: campaign for campaign in campaigns}
    product_map = {
        item.campaign_id: item.product_id
        for item in (
            await session.scalars(
                select(CampaignSkuMap).where(
                    CampaignSkuMap.campaign_id.in_(campaign_by_id)
                )
            )
        ).all()
    } if campaign_by_id else {}
    products = {
        row.id: row
        for row in (
            await session.scalars(select(Product).where(Product.brand_id == brand.id))
        ).all()
    }
    inventories = list(
        (
            await session.scalars(
                select(InventorySnapshot).order_by(InventorySnapshot.captured_at.desc())
            )
        ).all()
    )
    inventory_by_product = {}
    for item in inventories:
        inventory_by_product.setdefault(item.product_id, item)
    optimizer_campaigns = []
    response_curves = {}
    observed_days = (latest_fact_date - window_start).days + 1
    for campaign_id, observations in facts_by_campaign.items():
        campaign = campaign_by_id.get(campaign_id)
        if campaign is None:
            continue
        product_id = product_map.get(campaign.id)
        product = products.get(product_id)
        snapshot = inventory_by_product.get(product_id)
        cover = snapshot.on_hand / snapshot.units_per_day if snapshot and snapshot.units_per_day else 999
        points = [
            {"spend": observation["spend"], "value": observation["revenue"]}
            for observation in observations
            if observation["spend"] > 0 and observation["revenue"] >= 0
        ]
        if len(points) >= 3:
            revenue_curve = fit_response_curve(points)
            fit_method = "fitted_to_daily_revenue"
        else:
            average_spend = sum(item["spend"] for item in observations) / observed_days
            average_revenue = sum(item["revenue"] for item in observations) / observed_days
            revenue_curve = {
                "scale": round(2 * average_revenue, 4),
                "half_saturation": round(max(average_spend, 0.01), 4),
                "r_squared": 0.0,
            }
            fit_method = "recent_average_fallback"
        response_curves[campaign.id] = {**revenue_curve, "fit_method": fit_method}
        optimizer_campaigns.append({
            "campaign_id": campaign.id,
            "name": campaign.name,
            "daily_budget": float(campaign.daily_budget),
            "roas": (
                sum(item["revenue"] for item in observations)
                / max(sum(item["spend"] for item in observations), 0.01)
            ),
            "revenue_curve": revenue_curve,
            "cogs_rate": (
                sum(item["cogs"] for item in observations)
                / max(sum(item["revenue"] for item in observations), 0.01)
            ),
            "inventory_cover_days": cover,
        })
    result = optimize_budget(
        optimizer_campaigns,
        payload.total_budget,
        payload.max_shift_pct,
        payload.target_roas,
    )
    result["response_curves"] = response_curves
    result["data_window"] = {
        "start": window_start.isoformat(),
        "end": latest_fact_date.isoformat(),
        "days": observed_days,
    }
    result["budget_period"] = "daily"
    return result


def _project_campaign_day(
    metric: AdMetricDaily,
    daily_budget: float,
    unit_cost: float,
) -> dict[str, float]:
    utilization = min(1.0, max(0.1, metric.spend / max(daily_budget, 1)))
    spend = daily_budget * utilization
    baseline_roas = metric.attributed_revenue / metric.spend if metric.spend else 0
    relative_scale = spend / metric.spend if metric.spend else 1
    marginal_dampener = 1 / (1 + max(0, relative_scale - 1) * 0.18)
    revenue = spend * baseline_roas * marginal_dampener
    conversions = metric.conversions * relative_scale * marginal_dampener
    cogs = conversions * unit_cost
    return {
        "spend": spend,
        "revenue": revenue,
        "conversions": conversions,
        "cogs": cogs,
        "profit": revenue - cogs - spend,
    }


@router.post("/simulation/clock/advance")
async def advance_simulation_clock(
    payload: ClockAdvanceInput,
    session: AsyncSession = Depends(get_session),
):
    brand = await _brand(session)
    state = await session.scalar(
        select(SimulationState).where(SimulationState.brand_id == brand.id)
    )
    if state is None:
        raise HTTPException(status_code=503, detail="Simulation clock has not been initialized.")
    campaigns = list(
        (
            await session.scalars(
                select(Campaign).where(Campaign.brand_id == brand.id)
            )
        ).all()
    )
    campaign_ids = [campaign.id for campaign in campaigns]
    occupied_metric_dates = set()
    if campaign_ids:
        occupied_metric_dates = {
            (campaign_id, metric_date)
            for campaign_id, metric_date in (
                await session.execute(
                    select(AdMetricDaily.campaign_id, AdMetricDaily.metric_date).where(
                        AdMetricDaily.campaign_id.in_(campaign_ids),
                        AdMetricDaily.metric_date > state.simulated_date,
                        AdMetricDaily.metric_date
                        <= state.simulated_date + timedelta(days=payload.days),
                    )
                )
            ).all()
        }
    mappings = {
        item.campaign_id: item.product_id
        for item in (
            await session.scalars(
                select(CampaignSkuMap).where(CampaignSkuMap.campaign_id.in_(campaign_ids))
            )
        ).all()
    } if campaign_ids else {}
    products = {
        product.id: product
        for product in (
            await session.scalars(select(Product).where(Product.brand_id == brand.id))
        ).all()
    }
    recent_metric_ranks = (
        select(
            AdMetricDaily.campaign_id,
            AdMetricDaily.id.label("metric_id"),
            func.row_number().over(
                partition_by=AdMetricDaily.campaign_id,
                order_by=AdMetricDaily.metric_date.desc(),
            ).label("metric_rank"),
        )
        .where(
            AdMetricDaily.campaign_id.in_(campaign_ids),
            AdMetricDaily.metric_date <= state.simulated_date,
            AdMetricDaily.source_payload["clock_day"].as_string().is_(None),
        )
        .subquery()
    ) if campaign_ids else None
    recent_metrics = list(
        (
            await session.scalars(
                select(AdMetricDaily)
                .join(
                    recent_metric_ranks,
                    AdMetricDaily.id == recent_metric_ranks.c.metric_id,
                )
                .where(recent_metric_ranks.c.metric_rank <= 30)
                .order_by(
                    AdMetricDaily.campaign_id,
                    AdMetricDaily.metric_date.desc(),
                )
            )
        ).all()
    ) if recent_metric_ranks is not None else []
    metrics_by_campaign: dict[str, list[AdMetricDaily]] = {}
    for metric in recent_metrics:
        metrics_by_campaign.setdefault(metric.campaign_id, []).append(metric)
    last_metrics = {
        campaign_id: metrics[0]
        for campaign_id, metrics in metrics_by_campaign.items()
    }
    platform_ids = {campaign.platform_id for campaign in campaigns}
    platforms = list(
        (
            await session.scalars(
                select(Platform).where(Platform.id.in_(platform_ids))
            )
        ).all()
    ) if platform_ids else []
    platform_names = {platform.id: platform.name for platform in platforms}
    generated_profit = 0.0
    counterfactual_profit = 0.0
    for _ in range(payload.days):
        sim_date = state.simulated_date + timedelta(days=1)
        state.simulated_date = sim_date
        for campaign in campaigns:
            campaign_metrics = metrics_by_campaign.get(campaign.id, [])
            metric = next(
                (
                    item
                    for item in campaign_metrics
                    if item.metric_date.weekday() == sim_date.weekday()
                ),
                campaign_metrics[0] if campaign_metrics else None,
            )
            if metric is None:
                continue
            platform_name = platform_names.get(campaign.platform_id)
            if platform_name is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"Campaign {campaign.id} has no registered platform.",
                )
            product = products.get(mappings.get(campaign.id, ""))
            unit_cost = product.unit_cost if product else 0
            simulated = _project_campaign_day(
                metric,
                float(campaign.daily_budget),
                unit_cost,
            )
            baseline = _project_campaign_day(
                metric,
                max(float(metric.spend), 1),
                unit_cost,
            )
            daily_spend = simulated["spend"]
            revenue = simulated["revenue"]
            cogs = simulated["cogs"]
            profit = simulated["profit"]
            relative_scale = daily_spend / metric.spend if metric.spend else 1
            generated_profit += profit
            counterfactual_profit += baseline["profit"]
            metric_key = (campaign.id, sim_date)
            if metric_key not in occupied_metric_dates:
                session.add(
                    AdMetricDaily(
                        campaign_id=campaign.id,
                        metric_date=sim_date,
                        spend=daily_spend,
                        impressions=round(metric.impressions * relative_scale),
                        clicks=round(metric.clicks * relative_scale),
                        conversions=simulated["conversions"],
                        attributed_revenue=revenue,
                        source_payload={"simulated": True, "clock_day": sim_date.isoformat()},
                    )
                )
                occupied_metric_dates.add(metric_key)
            session.add(
                UnifiedFact(
                    brand_id=brand.id,
                    fact_date=sim_date,
                    platform=platform_name,
                    campaign_id=campaign.id,
                    product_id=product.id if product else None,
                    spend=daily_spend,
                    attributed_revenue=revenue,
                    reconciled_revenue=revenue,
                    cogs=cogs,
                    contribution_profit=profit,
                    clicks=round(metric.clicks * relative_scale),
                    impressions=round(metric.impressions * relative_scale),
                    conversions=simulated["conversions"],
                    reconciliation_factor=1,
                    source="simulator_clock",
                )
            )
        state.advanced_days += 1

    state.cumulative_profit_uplift += generated_profit - counterfactual_profit
    state.updated_at = datetime.now(timezone.utc)
    session.add(
        ModelRegistry(
            name="adpilot_clock_simulation_comparison",
            version=f"clock-{state.advanced_days}",
            metrics={
                "simulated_profit": round(generated_profit, 2),
                "baseline_profit": round(counterfactual_profit, 2),
            },
            parameters={
                "days_advanced": payload.days,
                "simulated_to_baseline_profit_ratio": round(
                    generated_profit / counterfactual_profit,
                    5,
                ) if abs(counterfactual_profit) > 1e-9 else 1,
            },
        )
    )
    executed_recommendations = list(
        (
            await session.scalars(
                select(Recommendation).where(
                    Recommendation.brand_id == brand.id,
                    Recommendation.status == "executed",
                )
            )
        ).all()
    )
    recommendation_by_id = {item.id: item for item in executed_recommendations}
    executions = list(
        (
            await session.scalars(
                select(Execution).where(
                    Execution.recommendation_id.in_(recommendation_by_id),
                    Execution.status == "executed",
                )
            )
        ).all()
    ) if recommendation_by_id else []
    existing_outcome_execution_ids = {
        execution_id
        for execution_id in (
            await session.scalars(
                select(Outcome.execution_id).where(
                    Outcome.execution_id.in_([item.id for item in executions])
                )
            )
        ).all()
    } if executions else set()
    measured_outcomes = []
    campaign_by_id = {item.id: item for item in campaigns}
    for execution in executions:
        recommendation = recommendation_by_id[execution.recommendation_id]
        if execution.id in existing_outcome_execution_ids:
            continue
        campaign_id = recommendation.campaign_id
        metric = last_metrics.get(campaign_id or "")
        before_budget = execution.before_state.get("daily_budget")
        after_budget = execution.after_state.get("daily_budget")
        campaign = campaign_by_id.get(campaign_id or "")
        if (
            metric is None
            or campaign is None
            or before_budget is None
            or after_budget is None
        ):
            continue
        product = products.get(mappings.get(campaign.id, ""))
        unit_cost = product.unit_cost if product else 0
        campaign_baseline_profit = (
            _project_campaign_day(metric, float(before_budget), unit_cost)["profit"]
            * payload.days
        )
        campaign_actual_profit = (
            _project_campaign_day(metric, float(after_budget), unit_cost)["profit"]
            * payload.days
        )
        uplift = campaign_actual_profit - campaign_baseline_profit
        session.add(
            Outcome(
                recommendation_id=recommendation.id,
                execution_id=execution.id,
                actual_profit=campaign_actual_profit,
                counterfactual_profit=campaign_baseline_profit,
                mape=0,
                payload={
                    "simulated_days": payload.days,
                    "simulated_date": state.simulated_date.isoformat(),
                    "campaign_id": campaign.id,
                    "scope": "executed_campaign",
                    "forecast_error_available": False,
                },
            )
        )
        measured_outcomes.append({
            "recommendation_id": recommendation.id,
            "title": recommendation.title,
            "actual_profit": round(campaign_actual_profit, 2),
            "counterfactual_profit": round(campaign_baseline_profit, 2),
            "profit_uplift": round(uplift, 2),
        })
    await session.commit()
    return {
        "simulated_date": _iso(state.simulated_date),
        "advanced_days": state.advanced_days,
        "actual_simulated_profit": round(generated_profit, 2),
        "counterfactual_profit": round(counterfactual_profit, 2),
        "profit_uplift": round(generated_profit - counterfactual_profit, 2),
        "cumulative_profit_uplift": round(state.cumulative_profit_uplift, 2),
        "mape_pct": None,
        "forecast_error_available": False,
        "comparison_recorded": True,
        "model_recalibrated": False,
        "measured_outcomes": measured_outcomes,
    }


@router.get("/learning")
async def learning(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    outcomes = list(
        (
            await session.scalars(
                select(Outcome).join(
                    Recommendation,
                    Recommendation.id == Outcome.recommendation_id,
                ).where(Recommendation.brand_id == brand.id)
            )
        ).all()
    )
    outcomes = [
        item for item in outcomes
        if item.payload.get("scope") == "executed_campaign"
    ]
    models = list(
        (
            await session.scalars(
                select(ModelRegistry).order_by(ModelRegistry.trained_at.desc()).limit(20)
            )
        ).all()
    )
    wins = sum(item.actual_profit > item.counterfactual_profit for item in outcomes)
    losses = sum(item.actual_profit < item.counterfactual_profit for item in outcomes)
    ties = len(outcomes) - wins - losses
    forecast_errors = [
        item.mape
        for item in outcomes
        if item.payload.get("forecast_error_available") is True
    ]
    state = await session.scalar(
        select(SimulationState).where(SimulationState.brand_id == brand.id)
    )
    return {
        "outcome_count": len(outcomes),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "win_rate": wins / len(outcomes) if outcomes else 0,
        "mean_absolute_percentage_error": (
            float(np.mean(forecast_errors)) if forecast_errors else None
        ),
        "forecast_error_count": len(forecast_errors),
        "cumulative_profit_uplift": state.cumulative_profit_uplift if state else 0,
        "models": [
            {
                "name": model.name,
                "version": model.version,
                "trained_at": _iso(model.trained_at),
                "metrics": model.metrics,
                "parameters": model.parameters,
            }
            for model in models
        ],
    }


@router.get("/sources")
async def source_health(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    platforms = list((await session.scalars(select(Platform))).all())
    facts = list(
        (
            await session.scalars(
                select(UnifiedFact).where(UnifiedFact.brand_id == brand.id)
            )
        ).all()
    )
    campaigns = list(
        (
            await session.scalars(
                select(Campaign).where(Campaign.brand_id == brand.id)
            )
        ).all()
    )
    campaigns_by_platform: dict[str, int] = {}
    platform_names = {platform.id: platform.name for platform in platforms}
    campaign_platforms: dict[str, str] = {}
    for campaign in campaigns:
        campaigns_by_platform[campaign.platform_id] = (
            campaigns_by_platform.get(campaign.platform_id, 0) + 1
        )
        if campaign.platform_id in platform_names:
            campaign_platforms[campaign.id] = platform_names[campaign.platform_id]
    facts_by_platform: dict[str, list[UnifiedFact]] = {}
    for fact in facts:
        source_name = campaign_platforms.get(fact.campaign_id, fact.platform)
        facts_by_platform.setdefault(source_name, []).append(fact)
    products_count = await session.scalar(
        select(func.count()).select_from(Product).where(Product.brand_id == brand.id)
    )
    orders_count = await session.scalar(
        select(func.count()).select_from(Order).where(Order.brand_id == brand.id)
    )
    attribute_fields = {
        "date": lambda row: row.fact_date is not None,
        "spend": lambda row: math.isfinite(row.spend) and row.spend >= 0,
        "revenue": lambda row: (
            math.isfinite(row.attributed_revenue)
            and row.attributed_revenue >= 0
            and math.isfinite(row.reconciled_revenue)
            and row.reconciled_revenue >= 0
        ),
        "impressions": lambda row: row.impressions >= 0,
        "clicks": lambda row: row.clicks >= 0,
        "conversions": lambda row: (
            math.isfinite(row.conversions) and row.conversions >= 0
        ),
    }
    platforms_by_key = {platform.key: platform for platform in platforms}
    sources = []
    connected_platform_ids: set[str] = set()
    for connector_key, connector in CONNECTORS.items():
        platform = platforms_by_key.get(connector.platform_key)
        if platform is None:
            continue
        connected_platform_ids.add(platform.id)
        sources.append(
            _source_health_payload(
                connector_key,
                platform,
                facts_by_platform.get(platform.name, []),
                campaigns_by_platform.get(platform.id, 0),
                products_count or 0,
                orders_count or 0,
                attribute_fields,
            )
        )
    for platform in platforms:
        if platform.id in connected_platform_ids:
            continue
        sources.append(
            _source_health_payload(
                platform.key,
                platform,
                facts_by_platform.get(platform.name, []),
                campaigns_by_platform.get(platform.id, 0),
                products_count or 0,
                orders_count or 0,
                attribute_fields,
                mode="import",
            )
        )
    return sources


def _source_health_payload(
    connector_key: str,
    platform: Platform,
    facts: list[UnifiedFact],
    campaign_count: int,
    products_count: int,
    orders_count: int,
    attribute_fields: dict[str, Any],
    mode: str = "simulator",
) -> dict[str, Any]:
    total = len(facts)
    attribute_health = {}
    for name, check in attribute_fields.items():
        accepted = sum(bool(check(fact)) for fact in facts)
        attribute_health[name] = {
            "accepted": accepted,
            "total": total,
            "health_score": accepted / total * 100 if total else 0,
        }
    attribute_records = sum(item["total"] for item in attribute_health.values())
    accepted_attributes = sum(item["accepted"] for item in attribute_health.values())
    health_score = (
        accepted_attributes / attribute_records * 100
        if attribute_records
        else 0
    )
    return {
        "key": connector_key,
        "name": platform.name,
        "mode": mode,
        "status": "healthy" if health_score == 100 else "degraded",
        "health_score": health_score,
        "attribute_health": attribute_health,
        "record_counts": {
            "platforms": 1,
            "campaigns": campaign_count,
            "products": products_count,
            "orders": orders_count,
            "facts": total,
        },
        "last_ingest": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/ingest/upload")
async def upload_data(
    file: UploadFile = File(...),
    column_mapping: str = Form(default="{}"),
    session: AsyncSession = Depends(get_session),
):
    brand = await _brand(session)
    if file.size and file.size > 10_000_000:
        raise HTTPException(status_code=413, detail="Uploads are limited to 10 MB.")
    if not file.filename or not file.filename.lower().endswith((".csv", ".json")):
        raise HTTPException(status_code=415, detail="Upload a CSV or JSON file.")
    raw = await file.read(10_000_001)
    if len(raw) > 10_000_000:
        raise HTTPException(status_code=413, detail="Uploads are limited to 10 MB.")
    try:
        decoded = raw.decode("utf-8-sig")
        if file.filename.lower().endswith(".csv"):
            frame = pd.read_csv(io.StringIO(decoded))
        else:
            parsed = json.loads(decoded)
            if not isinstance(parsed, list) or any(not isinstance(row, dict) for row in parsed):
                raise ValueError("JSON data must be a list of record objects.")
            frame = pd.DataFrame(parsed)
    except (UnicodeDecodeError, pd.errors.ParserError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse uploaded data: {exc}") from exc
    if frame.empty:
        raise HTTPException(status_code=422, detail="The uploaded file contains no records.")
    if len(frame) > 100_000:
        raise HTTPException(status_code=413, detail="Uploads are limited to 100,000 records.")
    aliases = {
        "date": ("date", "metric_date", "day"),
        "campaign": ("campaign", "campaign_name", "campaign_id"),
        "spend": ("spend", "ad_spend", "cost"),
        "revenue": ("revenue", "sales_revenue", "attributed_revenue"),
        "impressions": ("impressions",),
        "clicks": ("clicks", "estimated_clicks"),
        "conversions": ("conversions", "orders"),
    }
    try:
        requested_mapping = json.loads(column_mapping)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="Column mapping must be a JSON object.") from exc
    if not isinstance(requested_mapping, dict):
        raise HTTPException(status_code=422, detail="Column mapping must be a JSON object.")
    lowered = {str(column).casefold(): column for column in frame.columns}
    suggestions = {
        target: next((lowered[name] for name in names if name in lowered), None)
        for target, names in aliases.items()
    }
    mapping = {
        target: requested_mapping.get(target, suggestions[target])
        for target in aliases
    }
    invalid_mapping = {
        target: source for target, source in mapping.items()
        if source is not None and source not in frame.columns
    }
    if invalid_mapping:
        raise HTTPException(
            status_code=422,
            detail=f"Mapped columns do not exist in the uploaded file: {invalid_mapping}",
        )
    missing_required = [
        target for target in ("date", "spend", "revenue")
        if mapping[target] is None
    ]
    if missing_required:
        return {
            "status": "mapping_required",
            "columns": list(map(str, frame.columns)),
            "required": missing_required,
            "fields": list(aliases),
            "suggestions": mapping,
            "rows": len(frame),
        }
    try:
        normalized = {}
        for target in aliases:
            source = mapping[target]
            if source is None and target in {"impressions", "clicks", "conversions"}:
                normalized[target] = 0
            elif source is None:
                normalized[target] = "Unmapped"
            else:
                normalized[target] = frame[source]
        frame = pd.DataFrame(normalized)
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
        for column in ("spend", "revenue", "impressions", "clicks", "conversions"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
            if not np.isfinite(frame[column].to_numpy(dtype=float)).all():
                raise ValueError(f"{column} must contain finite numbers.")
            if (frame[column] < 0).any():
                raise ValueError(f"{column} cannot contain negative values.")
        if any(not value for value in frame["campaign"].astype(str).str.strip()):
            raise ValueError("Campaign names cannot be empty.")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"Uploaded columns failed validation: {exc}") from exc

    platform = await session.scalar(select(Platform).where(Platform.key == "simulator"))
    if platform is None:
        platform = Platform(key="simulator", name="CSV Import", simulator_enabled=True)
        session.add(platform)
        await session.flush()
        account = AdAccount(
            brand_id=brand.id,
            platform_id=platform.id,
            external_id="CSV-UPLOAD",
            name="CSV upload",
        )
        session.add(account)
        await session.flush()
    else:
        account = await session.scalar(
            select(AdAccount).where(
                AdAccount.brand_id == brand.id,
                AdAccount.platform_id == platform.id,
            )
        )
        if account is None:
            account = AdAccount(
                brand_id=brand.id,
                platform_id=platform.id,
                external_id="CSV-UPLOAD",
                name="CSV upload",
            )
            session.add(account)
            await session.flush()
    campaigns_by_name: dict[str, Campaign] = {}
    seen_campaign_dates: set[tuple[str, Any]] = set()
    imported_campaign_ids: list[str] = []
    for row_index, (_, row) in enumerate(frame.iterrows(), start=1):
        spend = float(row["spend"])
        revenue = float(row["revenue"])
        clicks = int(row["clicks"])
        impressions = int(row["impressions"])
        conversions = float(row["conversions"])
        metric_date = row["date"]
        campaign_name = str(row["campaign"])
        campaign_date = (campaign_name, metric_date)
        if campaign_date in seen_campaign_dates:
            row_campaign = Campaign(
                brand_id=brand.id,
                ad_account_id=account.id,
                platform_id=platform.id,
                external_id=f"CSV-{uuid4().hex[:12]}",
                name=f"{campaign_name[:160]} (duplicate {row_index})",
                objective="imported performance",
                status="active",
                daily_budget=0,
                metadata_json={"source_filename": file.filename, "imported": True},
            )
            session.add(row_campaign)
            await session.flush()
        else:
            seen_campaign_dates.add(campaign_date)
            row_campaign = campaigns_by_name.get(campaign_name)
            if row_campaign is None:
                row_campaign = Campaign(
                    brand_id=brand.id,
                    ad_account_id=account.id,
                    platform_id=platform.id,
                    external_id=f"CSV-{uuid4().hex[:12]}",
                    name=campaign_name[:190],
                    objective="imported performance",
                    status="active",
                    daily_budget=0,
                    metadata_json={"source_filename": file.filename, "imported": True},
                )
                campaigns_by_name[campaign_name] = row_campaign
                session.add(row_campaign)
                await session.flush()
        imported_campaign_ids.append(row_campaign.id)
        session.add(
            AdMetricDaily(
                campaign_id=row_campaign.id,
                metric_date=metric_date,
                spend=spend,
                impressions=impressions,
                clicks=clicks,
                conversions=conversions,
                attributed_revenue=revenue,
                source_payload={"upload": file.filename, "campaign": campaign_name},
            )
        )
        session.add(
            UnifiedFact(
                brand_id=brand.id,
                fact_date=metric_date,
                platform="CSV Import",
                campaign_id=row_campaign.id,
                spend=spend,
                attributed_revenue=revenue,
                reconciled_revenue=revenue,
                contribution_profit=revenue - spend,
                clicks=clicks,
                impressions=impressions,
                conversions=conversions,
                reconciliation_factor=1,
                source="csv_upload",
            )
        )
    await session.commit()
    attribute_health = {
        target: {
            "accepted": len(frame),
            "total": len(frame),
            "health_score": len(frame) / len(frame) * 100,
        }
        for target, source in mapping.items()
        if source is not None
    }
    return {
        "status": "imported",
        "campaign_id": imported_campaign_ids[0],
        "rows": len(frame),
        "accepted_records": len(frame),
        "total_records": len(frame),
        "attribute_health": attribute_health,
    }


@router.get("/anomaly-methods")
async def anomaly_methods(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    facts = list(
        (
            await session.scalars(
                select(UnifiedFact)
                .where(UnifiedFact.brand_id == brand.id)
                .order_by(UnifiedFact.fact_date)
            )
        ).all()
    )
    campaigns = {
        campaign.id: campaign.name
        for campaign in (
            await session.scalars(select(Campaign).where(Campaign.brand_id == brand.id))
        ).all()
    }
    by_campaign: dict[str, list[UnifiedFact]] = {}
    for fact in facts:
        if fact.campaign_id:
            by_campaign.setdefault(fact.campaign_id, []).append(fact)
    return {
        "campaigns": [
            {
                "campaign_id": campaign_id,
                "campaign": campaigns.get(campaign_id, "Unknown campaign"),
                "dates": [fact.fact_date.isoformat() for fact in rows],
                "robust_z_mad": robust_anomaly_scores(
                    [float(fact.contribution_profit) for fact in rows]
                ),
                "stl_residual": stl_residual_scores(
                    [float(fact.contribution_profit) for fact in rows]
                ),
                "pelt_changepoints": changepoint_indices(
                    [float(fact.contribution_profit) for fact in rows]
                ),
            }
            for campaign_id, rows in by_campaign.items()
        ],
        "observations": len(facts),
    }


@router.get("/optimization/curve")
async def optimization_curve(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    campaigns = list(
        (await session.scalars(select(Campaign).where(Campaign.brand_id == brand.id))).all()
    )
    return [
        {
            "campaign_id": campaign.id,
            "name": campaign.name,
            **fit_response_curve(
                [
                    {
                        "spend": max(1, campaign.daily_budget * factor),
                        "value": campaign.daily_budget * factor * (1 + factor) ** -0.7,
                    }
                    for factor in (0.25, 0.5, 0.75, 1, 1.25)
                ]
            ),
        }
        for campaign in campaigns
    ]


@router.post("/pipeline/run")
async def run_pipeline(session: AsyncSession = Depends(get_session)):
    brand = await _brand(session)
    ingest_count = await ingest_simulator_data(session, brand)
    campaigns = list(
        (await session.scalars(select(Campaign).where(Campaign.brand_id == brand.id))).all()
    )
    facts = list(
        (
            await session.scalars(
                select(UnifiedFact)
                .where(UnifiedFact.brand_id == brand.id)
                .order_by(UnifiedFact.campaign_id, UnifiedFact.fact_date)
            )
        ).all()
    )
    recommendations = list(
        (
            await session.scalars(
                select(Recommendation).where(
                    Recommendation.brand_id == brand.id,
                    Recommendation.status == "proposed",
                )
            )
        ).all()
    )
    campaign_by_id = {campaign.id: campaign for campaign in campaigns}
    grouped_facts: dict[str, list[UnifiedFact]] = {}
    for fact in facts:
        if fact.campaign_id:
            grouped_facts.setdefault(fact.campaign_id, []).append(fact)
    anomalies = list(
        (
            await session.scalars(select(Anomaly).where(Anomaly.brand_id == brand.id))
        ).all()
    )
    anomalies_by_key = {item.scenario_key: item for item in anomalies if item.scenario_key}
    run_id = str(uuid4())
    campaign_diagnostics = []
    for campaign_id, rows in grouped_facts.items():
        campaign = campaign_by_id.get(campaign_id)
        if campaign is None:
            continue
        profits = [float(row.contribution_profit) for row in rows]
        robust = robust_anomaly_scores(profits)
        stl = stl_residual_scores(profits)
        change_points = changepoint_indices(profits)
        latest = rows[-1]
        baseline_rows = rows[-8:-1] or rows[:-1]
        previous = {
            "ctr": sum(row.clicks for row in baseline_rows) / max(sum(row.impressions for row in baseline_rows), 1),
            "cvr": sum(row.conversions for row in baseline_rows) / max(sum(row.clicks for row in baseline_rows), 1),
            "aov": sum(row.reconciled_revenue for row in baseline_rows) / max(sum(row.conversions for row in baseline_rows), 1),
            "cpc": sum(row.spend for row in baseline_rows) / max(sum(row.clicks for row in baseline_rows), 1),
        }
        current = {
            "ctr": latest.clicks / max(latest.impressions, 1),
            "cvr": latest.conversions / max(latest.clicks, 1),
            "aov": latest.reconciled_revenue / max(latest.conversions, 1),
            "cpc": latest.spend / max(latest.clicks, 1),
        }
        decomposition = decompose_roas(current, previous)
        scenario_key = campaign.metadata_json.get("scenario_key")
        anomaly = anomalies_by_key.get(scenario_key)
        if anomaly:
            anomaly.evidence = {
                **anomaly.evidence,
                "latest_metrics": {
                    "spend": round(float(latest.spend), 2),
                    "revenue": round(float(latest.reconciled_revenue), 2),
                    "contribution_profit": round(float(latest.contribution_profit), 2),
                    "roas": round(
                        float(latest.reconciled_revenue / latest.spend)
                        if latest.spend else 0,
                        3,
                    ),
                },
                "statistical_scores": {
                    "robust_z_mad": robust[-1] if robust else 0,
                    "stl_residual": stl[-1] if stl else 0,
                    "pelt_changepoints": change_points,
                },
                "decomposition": decomposition,
            }
        campaign_diagnostics.append({
            "campaign_id": campaign_id,
            "scenario_key": scenario_key,
            "rows": len(rows),
            "robust_z_mad": robust[-1] if robust else 0,
            "stl_residual": stl[-1] if stl else 0,
            "pelt_changepoints": change_points,
            "decomposition": decomposition,
        })

    steps = [
        {
            "tool": "fetch_incremental",
            "output": {"status": "completed", "campaigns_ingested": ingest_count},
        },
        {
            "tool": "query_unified_facts",
            "output": {"status": "completed", "fact_rows": len(facts), "campaigns": len(campaigns)},
        },
        {
            "tool": "run_decomposition",
            "output": {
                "status": "completed",
                "campaigns_analyzed": len(campaign_diagnostics),
                "diagnostics": campaign_diagnostics,
            },
        },
        {
            "tool": "evaluate_decisions",
            "output": {
                "status": "completed",
                "proposed_recommendations": len(recommendations),
                "inventory_guarded": sum(
                    1 for item in recommendations
                    if item.constraints.get("inventory_cover_days") is not None
                    and item.constraints["inventory_cover_days"] < 5
                ),
            },
        },
    ]
    for index, step in enumerate(steps, start=1):
        session.add(
            AgentStep(
                brand_id=brand.id,
                run_id=run_id,
                step_index=index,
                tool_name=step["tool"],
                input={"campaign_count": len(campaigns), "fact_count": len(facts)},
                output=step["output"],
            )
        )
    await session.commit()
    return {"run_id": run_id, "status": "completed", "steps": steps}
