from datetime import datetime, time, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from redis.asyncio import Redis

from src.adpilot.connectors import CONNECTORS
from src.adpilot.db import SessionLocal
from src.adpilot.models import (
    AdMetricDaily,
    AdMetricHourly,
    AgentStep,
    Brand,
    Campaign,
    CampaignSkuMap,
    InventorySnapshot,
    Order,
    Platform,
    Product,
    UnifiedFact,
)
from src.adpilot.seed import (
    BRAND_NAME,
    branded_search_observation,
    daily_observation,
)


async def ingest_simulator_data(
    session: AsyncSession,
    brand: Brand,
    now: datetime | None = None,
) -> int:
    now = (now or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    metric_date = now.date()
    campaigns = list(
        (
            await session.scalars(
                select(Campaign).where(Campaign.brand_id == brand.id)
            )
        ).all()
    )
    mappings = {
        item.campaign_id: item
        for item in (
            await session.scalars(
                select(CampaignSkuMap).where(
                    CampaignSkuMap.campaign_id.in_([item.id for item in campaigns])
                )
            )
        ).all()
    } if campaigns else {}
    products = {
        item.id: item
        for item in (
            await session.scalars(
                select(Product).where(Product.brand_id == brand.id)
            )
        ).all()
    }
    ingested = 0
    for campaign in campaigns:
        if campaign.metadata_json.get("imported"):
            continue
        platform = await session.get(Platform, campaign.platform_id)
        connector = CONNECTORS.get(platform.key) if platform else None
        if connector is None or not await connector.authenticate():
            continue
        scenario_key = campaign.metadata_json.get("scenario_key")
        if campaign.metadata_json.get("campaign_role") == "branded_search":
            record = branded_search_observation(29)
            baseline_budget = float(campaign.daily_budget)
        else:
            scenario_connector = next(
                (
                    item
                    for item in CONNECTORS.values()
                    if item.scenario_key == scenario_key
                ),
                None,
            )
            if scenario_connector is not None:
                connector = scenario_connector
                record = daily_observation(
                    connector.baseline,
                    scenario_key,
                    29,
                )
            else:
                records = await connector.normalize(
                    await connector.fetch_incremental(metric_date)
                )
                if not records:
                    continue
                record = records[0]
            baseline_budget = float(connector.baseline.get("daily_budget", 0))
        budget_factor = (
            max(0.0, float(campaign.daily_budget) / baseline_budget)
            if baseline_budget > 0
            else 1.0
        )
        spend = float(record["spend"]) * budget_factor
        revenue = float(record["revenue"]) * budget_factor**0.7
        clicks = round(float(record["clicks"]) * budget_factor**0.8)
        impressions = round(float(record["impressions"]) * budget_factor**0.8)
        conversions = float(record["conversions"]) * budget_factor**0.8

        hourly = await session.scalar(
            select(AdMetricHourly).where(
                AdMetricHourly.campaign_id == campaign.id,
                AdMetricHourly.metric_hour == now,
            )
        )
        hourly_values = {
            "spend": spend / 24,
            "clicks": round(clicks / 24),
            "impressions": round(impressions / 24),
            "conversions": conversions / 24,
            "attributed_revenue": revenue / 24,
        }
        if hourly is None:
            session.add(
                AdMetricHourly(
                    campaign_id=campaign.id,
                    metric_hour=now,
                    **hourly_values,
                )
            )
        else:
            for key, value in hourly_values.items():
                setattr(hourly, key, value)

        daily = await session.scalar(
            select(AdMetricDaily).where(
                AdMetricDaily.campaign_id == campaign.id,
                AdMetricDaily.metric_date == metric_date,
            )
        )
        daily_values = {
            "spend": spend,
            "impressions": impressions,
            "clicks": clicks,
            "conversions": conversions,
            "attributed_revenue": revenue,
            "source_payload": {
                **record,
                "simulator": True,
                "budget_factor": budget_factor,
                "ingested_at": now.isoformat(),
            },
        }
        if daily is None:
            session.add(
                AdMetricDaily(
                    campaign_id=campaign.id,
                    metric_date=metric_date,
                    **daily_values,
                )
            )
        else:
            for key, value in daily_values.items():
                setattr(daily, key, value)

        mapping = mappings.get(campaign.id)
        product = products.get(mapping.product_id) if mapping else None
        if product is not None:
            order_id = f"SIM-ORDER-{campaign.external_id}-{metric_date.isoformat()}"
            order = await session.scalar(
                select(Order).where(
                    Order.brand_id == brand.id,
                    Order.external_id == order_id,
                )
            )
            quantity = max(0, round(conversions))
            net_revenue = revenue * 0.84
            discount_pct = float(record.get("discount", 0))
            order_values = {
                "ordered_at": datetime.combine(metric_date, time(18), timezone.utc),
                "sku": product.sku,
                "quantity": quantity,
                "net_revenue": net_revenue,
                "cogs": quantity * product.unit_cost,
                "discount": net_revenue * discount_pct,
                "currency_code": brand.currency_code,
                "source": "simulator",
            }
            if order is None:
                session.add(
                    Order(
                        brand_id=brand.id,
                        external_id=order_id,
                        **order_values,
                    )
                )
            else:
                for key, value in order_values.items():
                    setattr(order, key, value)
            fact = await session.scalar(
                select(UnifiedFact).where(
                    UnifiedFact.brand_id == brand.id,
                    UnifiedFact.campaign_id == campaign.id,
                    UnifiedFact.fact_date == metric_date,
                )
            )
            fact_values = {
                "platform": platform.name if platform else "Unknown",
                "product_id": product.id,
                "spend": spend,
                "attributed_revenue": revenue,
                "reconciled_revenue": 0,
                "cogs": 0,
                "discounts": 0,
                "contribution_profit": -spend,
                "clicks": clicks,
                "impressions": impressions,
                "conversions": conversions,
                "reconciliation_factor": 0,
                "source": "simulator",
            }
            if fact is None:
                session.add(
                    UnifiedFact(
                        brand_id=brand.id,
                        campaign_id=campaign.id,
                        fact_date=metric_date,
                        **fact_values,
                    )
                )
            else:
                for key, value in fact_values.items():
                    setattr(fact, key, value)
        ingested += 1

    await session.flush()
    product_by_sku = {product.sku: product for product in products.values()}
    orders = list(
        (
            await session.scalars(select(Order).where(Order.brand_id == brand.id))
        ).all()
    )
    actual_by_product: dict[str, dict[str, float]] = {}
    for order in orders:
        if order.ordered_at.date() != metric_date:
            continue
        product = product_by_sku.get(order.sku)
        if product is None:
            continue
        totals = actual_by_product.setdefault(
            product.id,
            {"revenue": 0.0, "cogs": 0.0, "discounts": 0.0},
        )
        totals["revenue"] += order.net_revenue
        totals["cogs"] += order.cogs
        totals["discounts"] += order.discount

    facts = list(
        (
            await session.scalars(
                select(UnifiedFact).where(
                    UnifiedFact.brand_id == brand.id,
                    UnifiedFact.fact_date == metric_date,
                    UnifiedFact.product_id.is_not(None),
                )
            )
        ).all()
    )
    reported_by_product: dict[str, float] = {}
    for fact in facts:
        reported_by_product[fact.product_id] = (
            reported_by_product.get(fact.product_id, 0.0) + fact.attributed_revenue
        )
    for fact in facts:
        reported = reported_by_product.get(fact.product_id, 0.0)
        actual = actual_by_product.get(fact.product_id, {})
        factor = min(1.0, actual.get("revenue", 0.0) / reported) if reported else 0.0
        share = fact.attributed_revenue / reported if reported else 0.0
        fact.reconciliation_factor = factor
        fact.reconciled_revenue = fact.attributed_revenue * factor
        fact.cogs = actual.get("cogs", 0.0) * share
        fact.discounts = actual.get("discounts", 0.0) * share
        fact.contribution_profit = (
            fact.reconciled_revenue - fact.cogs - fact.discounts - fact.spend
        )
    return ingested


async def run_scheduled_simulator_ingestion(
    redis_client: Redis | None = None,
) -> dict[str, Any]:
    lock = redis_client.lock("adpilot:simulator-ingestion", timeout=300) if redis_client else None
    if lock is not None and not await lock.acquire(blocking=False):
        return {"status": "skipped", "reason": "another ingestion run holds the Redis lock"}
    try:
        async with SessionLocal() as session:
            brand = await session.scalar(select(Brand).where(Brand.name == BRAND_NAME))
            if brand is None:
                return {"status": "skipped", "reason": "demo brand is not initialized"}
            now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            ingested = await ingest_simulator_data(session, brand, now)
            campaigns = list(
                (
                    await session.scalars(
                        select(Campaign).where(Campaign.brand_id == brand.id)
                    )
                ).all()
            )
            for campaign in campaigns:
                if campaign.metadata_json.get("imported"):
                    continue
                platform = await session.get(Platform, campaign.platform_id)
                connector = CONNECTORS.get(platform.key) if platform else None
                if connector is None:
                    continue
                session.add(
                    AgentStep(
                        brand_id=brand.id,
                        run_id=f"scheduled-{now.isoformat()}",
                        step_index=1,
                        tool_name="scheduled_incremental_ingest",
                        input={"campaign_id": campaign.external_id, "connector": connector.key},
                        output={"status": "completed", "metric_hour": now.isoformat()},
                    )
                )
            await session.commit()
            return {"status": "completed", "campaigns_ingested": ingested}
    finally:
        if lock is not None and await lock.owned():
            await lock.release()
