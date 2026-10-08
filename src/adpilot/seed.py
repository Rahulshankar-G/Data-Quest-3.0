import hashlib
import math
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.adpilot.auth import hash_password
from src.adpilot.connectors import CONNECTORS, PLATFORMS, SCENARIO_CATALOG
from src.adpilot.models import (
    Ad,
    AdAccount,
    AdMetricDaily,
    AdSet,
    AgentStep,
    Anomaly,
    Brand,
    BusinessObjective,
    Campaign,
    CampaignSkuMap,
    Creative,
    Diagnosis,
    GaEvent,
    InventorySnapshot,
    ModelRegistry,
    Opportunity,
    Order,
    Platform,
    Product,
    Recommendation,
    Role,
    SkuPriceHistory,
    SimulationState,
    UnifiedFact,
    User,
)

BRAND_NAME = "AdPilot Demo Brand"


def stable_jitter(seed: str, day_offset: int) -> float:
    digest = hashlib.sha256(f"{seed}:{day_offset}".encode()).digest()
    return 0.94 + int.from_bytes(digest[:2], "big") / 65535 * 0.12


def daily_observation(
    baseline: dict,
    scenario: str,
    offset: int,
) -> dict:
    row = baseline.copy()
    jitter = stable_jitter(scenario, offset)
    progress = (offset + 1) / 30
    row["spend"] *= jitter
    row["impressions"] = round(row["impressions"] * jitter)
    row["clicks"] = round(row["clicks"] * jitter)
    row["conversions"] *= jitter
    row["revenue"] *= jitter

    if scenario == "meta_creative_fatigue":
        fatigue = max(0.25, 1 - progress * 0.52)
        row["clicks"] = round(row["clicks"] * fatigue)
        row["conversions"] = row["conversions"] * fatigue * 0.82
        row["revenue"] = row["revenue"] * fatigue * 0.82
        row["frequency"] = 1.8 + progress * 4
    elif scenario == "stockout_risk":
        row["inventory"] = max(5, baseline["inventory"] - round(offset * baseline["units_per_day"] / 30))
    elif scenario == "price_promo_cvr_drop":
        if offset >= 20:
            row["conversions"] *= 0.58
            row["revenue"] *= 0.58
            row["price"] = baseline["price"] * 1.16
        else:
            row["price"] = baseline["price"] * 0.9
            row["discount"] = 0.1
    elif scenario == "shopping_brand_cannibalization":
        row["revenue"] *= 1 + progress * 0.28
        row["branded_revenue"] = baseline["revenue"] * (1 - progress * 0.4)
    elif scenario == "tiktok_uncapped_headroom":
        row["spend"] *= 0.75 + progress * 0.22
        row["revenue"] *= 1.15 + progress * 0.5
        row["marginal_roas"] = 1.7 + progress * 2.2
    elif scenario == "ga4_tracking_outage":
        if offset >= 23:
            row["ga_events"] = max(0, round(row["conversions"] * 0.15))
        else:
            row["ga_events"] = round(row["conversions"])
    elif scenario == "attribution_overstatement":
        row["revenue"] = baseline["revenue"] * 1.9 * jitter
        row["order_revenue"] = baseline["revenue"] * 0.75 * jitter
    elif scenario == "high_margin_underpromoted":
        row["spend"] = baseline["spend"] * (0.8 + progress * 0.2) * jitter
        row["revenue"] = baseline["revenue"] * (0.95 + progress * 0.15) * jitter
    return row


def branded_search_observation(offset: int) -> dict:
    progress = (offset + 1) / 30
    jitter = stable_jitter("branded-search", offset)
    revenue = 2250 * (1 - progress * 0.42) * jitter
    return {
        "spend": 1120 * jitter,
        "impressions": round(36000 * jitter),
        "clicks": round(980 * jitter),
        "conversions": 23 * (1 - progress * 0.35) * jitter,
        "revenue": revenue,
        "order_revenue": revenue * 0.88,
    }


async def _ensure_demo_users(session: AsyncSession, brand: Brand, roles: list[Role]) -> None:
    role_lookup = {item.name: item for item in roles}
    demo_users = [
        {
            "username": "admin",
            "email": "admin@example.com",
            "display_name": "Admin User",
            "role_name": "admin",
            "password": "Admin@123",
        },
        {
            "username": "user",
            "email": "user@example.com",
            "display_name": "Standard User",
            "role_name": "operator",
            "password": "User@123",
        },
        {
            "username": "operator",
            "email": "operator@adpilot.local",
            "display_name": "Demo Operator",
            "role_name": "operator",
            "password": "Operator@123",
        },
    ]
    for item in demo_users:
        existing = await session.scalar(
            select(User).where((User.email == item["email"]) | (User.username == item["username"]))
        )
        role = role_lookup.get(item["role_name"])
        if existing is None:
            session.add(
                User(
                    brand_id=brand.id,
                    role_id=role.id,
                    username=item["username"],
                    email=item["email"],
                    password_hash=hash_password(item["password"]),
                    display_name=item["display_name"],
                    active=True,
                )
            )
            continue
        if not existing.password_hash:
            existing.password_hash = hash_password(item["password"])
        existing.role_id = role.id
        existing.display_name = item["display_name"]
        existing.username = item["username"]
        existing.active = True


async def seed_demo(session: AsyncSession) -> dict[str, int]:
    today = datetime.now(timezone.utc).date()
    existing_brand = await session.scalar(select(Brand).where(Brand.name == BRAND_NAME))
    if existing_brand is not None:
        return {"seeded": 0, "brand_id": existing_brand.id}

    brand = Brand(name=BRAND_NAME, currency_code="USD", timezone="America/New_York")
    session.add(brand)
    await session.flush()

    roles = [
        Role(name="admin", permissions=["read", "write", "execute", "configure"]),
        Role(name="analyst", permissions=["read", "diagnose", "recommend"]),
        Role(name="operator", permissions=["read", "approve", "execute_simulation"]),
    ]
    for role in roles:
        existing_role = await session.scalar(select(Role).where(Role.name == role.name))
        if existing_role is None:
            session.add(role)
    await session.flush()
    persisted_roles = list((await session.scalars(select(Role))).all())
    await _ensure_demo_users(session, brand, persisted_roles)

    platform_rows = []
    for key, name in PLATFORMS:
        existing_platform = await session.scalar(select(Platform).where(Platform.key == key))
        if existing_platform is None:
            platform_rows.append(Platform(key=key, name=name, simulator_enabled=True))
        else:
            platform_rows.append(existing_platform)
    session.add_all(platform_rows)
    await session.flush()
    platforms = {row.key: row for row in platform_rows}
    connector_records: dict[str, dict] = {
        connector.scenario_key: connector.baseline
        for connector in CONNECTORS.values()
        if connector.scenario_key != "demo"
    }

    products_by_sku: dict[str, Product] = {}
    campaigns_by_key: dict[str, Campaign] = {}
    account_by_platform: dict[str, AdAccount] = {}
    creatives_by_platform: dict[str, Creative] = {}
    for platform_key, platform_name in PLATFORMS:
        account = AdAccount(
            brand_id=brand.id,
            platform_id=platforms[platform_key].id,
            external_id=f"SIM-{platform_key.upper()}-001",
            name=f"{platform_name} simulator account",
            currency_code="USD",
        )
        session.add(account)
        account_by_platform[platform_key] = account
        creative = Creative(
            platform_id=platforms[platform_key].id,
            external_id=f"SIM-CR-{platform_key.upper()}",
            name=f"{platform_name} demo creative",
            format="video" if platform_key in {"meta", "tiktok"} else "product_listing",
            first_seen=datetime.now(timezone.utc) - timedelta(days=30),
        )
        session.add(creative)
        creatives_by_platform[platform_key] = creative

    for scenario in SCENARIO_CATALOG:
        connector_key = scenario["connector"]
        connector = CONNECTORS[connector_key]
        baseline = connector.baseline
        sku = baseline["sku"]
        if sku not in products_by_sku:
            product = Product(
                brand_id=brand.id,
                sku=sku,
                name=f"AdPilot {sku.removeprefix('SKU-').replace('-', ' ').title()}",
                category=sku.removeprefix("SKU-").split("-")[0].title(),
                unit_cost=float(baseline["unit_cost"]),
                price=float(baseline["price"]),
                currency_code="USD",
            )
            session.add(product)
            await session.flush()
            products_by_sku[sku] = product
        product = products_by_sku[sku]
        platform_key = connector.platform_key
        platform = platforms[platform_key]
        account = account_by_platform[platform_key]
        campaign = Campaign(
            brand_id=brand.id,
            ad_account_id=account.id,
            platform_id=platform.id,
            external_id=f"SIM-C-{scenario['key']}",
            name=scenario["title"],
            objective="conversions",
            status="active",
            daily_budget=float(baseline["daily_budget"]),
            metadata_json={
                "scenario_key": scenario["key"],
                "simulator_mode": True,
                "attribution_window_days": 7,
            },
        )
        session.add(campaign)
        await session.flush()
        campaigns_by_key[scenario["key"]] = campaign
        ad_set = AdSet(
            campaign_id=campaign.id,
            external_id=f"SIM-AS-{scenario['key']}",
            name=f"{scenario['title']} ad set",
        )
        session.add(ad_set)
        await session.flush()
        ad = Ad(
            ad_set_id=ad_set.id,
            creative_id=creatives_by_platform[platform_key].id,
            external_id=f"SIM-AD-{scenario['key']}",
            name=f"{scenario['title']} primary ad",
        )
        session.add(ad)
        session.add(
            CampaignSkuMap(
                campaign_id=campaign.id,
                product_id=product.id,
                weight=1.0,
                mapping_source="seeded_demo",
            )
        )
        session.add(
            SkuPriceHistory(
                product_id=product.id,
                effective_at=datetime.now(timezone.utc) - timedelta(days=30),
                price=float(baseline["price"]),
                discount_pct=float(baseline.get("discount", 0)),
            )
        )
        scenario_data = connector_records.get(scenario["key"], baseline)
        for offset in range(30):
            observed = daily_observation(scenario_data, scenario["key"], offset)
            observation_date = today - timedelta(days=29 - offset)
            spend = float(observed["spend"])
            revenue = float(observed["revenue"])
            clicks = max(0, round(observed["clicks"]))
            impressions = max(0, round(observed["impressions"]))
            conversions = float(observed["conversions"])
            metric = AdMetricDaily(
                campaign_id=campaign.id,
                metric_date=observation_date,
                spend=spend,
                impressions=impressions,
                clicks=clicks,
                conversions=conversions,
                attributed_revenue=revenue,
                source_payload={
                    "scenario_key": scenario["key"],
                    "frequency": observed.get("frequency"),
                    "ga4_purchase_events": observed.get("ga_events"),
                    "simulator": True,
                },
            )
            session.add(metric)
            order_revenue = float(
                observed.get("order_revenue", observed["revenue"] * 0.84)
            )
            units = max(1, round(observed["conversions"]))
            session.add(
                Order(
                    brand_id=brand.id,
                    external_id=f"SIM-ORDER-{scenario['key']}-{observation_date.isoformat()}",
                    ordered_at=datetime.combine(observation_date, time(12), timezone.utc),
                    sku=sku,
                    quantity=units,
                    net_revenue=order_revenue,
                    cogs=units * float(product.unit_cost),
                    discount=float(observed.get("discount", 0)) * order_revenue,
                    currency_code="USD",
                    source="simulator",
                )
            )
            session.add(
                InventorySnapshot(
                    product_id=product.id,
                    captured_at=datetime.combine(observation_date, time(23), timezone.utc),
                    on_hand=int(observed["inventory"]),
                    units_per_day=float(observed["units_per_day"]),
                )
            )
            session.add(
                UnifiedFact(
                    brand_id=brand.id,
                    fact_date=observation_date,
                    platform=platform.name,
                    campaign_id=campaign.id,
                    product_id=product.id,
                    spend=spend,
                    attributed_revenue=revenue,
                    reconciled_revenue=order_revenue,
                    cogs=units * float(product.unit_cost),
                    discounts=float(observed.get("discount", 0)) * order_revenue,
                    contribution_profit=(
                        order_revenue
                        - units * float(product.unit_cost)
                        - float(observed.get("discount", 0)) * order_revenue
                        - spend
                    ),
                    clicks=clicks,
                    impressions=impressions,
                    conversions=conversions,
                    reconciliation_factor=order_revenue / revenue if revenue else 0,
                    source="simulator",
                )
            )
            if scenario["key"] == "ga4_tracking_outage":
                session.add(
                    GaEvent(
                        brand_id=brand.id,
                        event_at=datetime.combine(observation_date, time(18), timezone.utc),
                        event_name="purchase",
                        source="ga4",
                        campaign_external_id=campaign.external_id,
                        revenue=float(observed.get("ga_events", observed["conversions"])),
                        payload={"scenario_key": scenario["key"], "simulator": True},
                    )
                )

        on_hand = int(baseline["inventory"])
        units_per_day = float(baseline["units_per_day"])
        cover = on_hand / units_per_day if units_per_day else math.inf
        anomaly = Anomaly(
            brand_id=brand.id,
            scenario_key=scenario["key"],
            severity=str(baseline["severity"]),
            metric="inventory_cover" if scenario["key"] == "stockout_risk" else "contribution_profit",
            score=float(baseline["priority"]),
            evidence={
                "title": scenario["title"],
                "summary": baseline["summary"],
                "drivers": baseline["drivers"],
                "platform": platform.name,
                "campaign_id": campaign.id,
                "sku": sku,
                "inventory": on_hand,
                "units_per_day": units_per_day,
                "spend": baseline["spend"],
                "revenue": baseline["revenue"],
                "ctr": baseline["clicks"] / baseline["impressions"],
                "cvr": baseline["conversions"] / baseline["clicks"],
                "cpa": baseline["spend"] / baseline["conversions"],
                "roas": baseline["revenue"] / baseline["spend"],
                "inventory_cover_days": cover,
                "status": "simulator_seeded",
                "scenario_category": scenario["category"],
            },
        )
        session.add(anomaly)
        await session.flush()
        diagnosis = Diagnosis(
            anomaly_id=anomaly.id,
            summary=baseline["summary"],
            drivers=baseline["drivers"],
            decomposition={
                "ctr": baseline["clicks"] / baseline["impressions"],
                "cvr": baseline["conversions"] / baseline["clicks"],
                "aov": baseline["revenue"] / baseline["conversions"],
                "cpc": baseline["spend"] / baseline["clicks"],
                "roas_identity": baseline["revenue"] / baseline["spend"],
            },
            confidence=0.86,
        )
        session.add(diagnosis)
        opportunity = Opportunity(
            brand_id=brand.id,
            scenario_key=scenario["key"],
            title=scenario["title"],
            kind=scenario["category"],
            estimated_profit_uplift=max(
                -1000,
                (
                    baseline["revenue"]
                    * (baseline["price"] - baseline["unit_cost"])
                    / baseline["price"]
                    - baseline["spend"]
                )
                * baseline["change_pct"] / 100,
            ),
            confidence=0.78,
            features={
                "inventory_cover_days": round(cover, 2) if math.isfinite(cover) else None,
                "ctr": baseline["clicks"] / baseline["impressions"],
                "cvr": baseline["conversions"] / baseline["clicks"],
                "aov": baseline["revenue"] / baseline["conversions"],
                "cpc": baseline["spend"] / baseline["clicks"],
                "marginal_roas": baseline.get("marginal_roas"),
            },
        )
        session.add(opportunity)
        await session.flush()
        constraints = {
            "inventory_cover_days": round(cover, 2) if math.isfinite(cover) else None,
            "min_inventory_cover_days": 5,
            "max_budget_shift_pct": 25,
            "requires_operator_approval": True,
        }
        session.add(
            Recommendation(
                brand_id=brand.id,
                opportunity_id=opportunity.id,
                campaign_id=campaign.id,
                scenario_key=scenario["key"],
                title=f"Review: {scenario['title']}",
                rationale=baseline["summary"],
                proposed_change_pct=float(baseline["change_pct"]),
                priority_score=float(baseline["priority"]),
                constraints=constraints,
                payload={
                    "campaign_id": campaign.id,
                    "campaign_name": campaign.name,
                    "daily_budget": baseline["daily_budget"],
                    "drivers": baseline["drivers"],
                    "scenario_key": scenario["key"],
                },
            )
        )
        session.add(
            AgentStep(
                brand_id=brand.id,
                run_id=anomaly.id,
                step_index=1,
                tool_name="query_unified_facts",
                input={"scenario_key": scenario["key"]},
                output={
                    "rows_checked": 30,
                    "platform": platform.name,
                    "campaign_id": campaign.external_id,
                },
            )
        )

    shopping_campaign = campaigns_by_key["shopping_brand_cannibalization"]
    shopping_product = products_by_sku["SKU-SEARCH"]
    google_platform = platforms["google"]
    brand_search = Campaign(
        brand_id=brand.id,
        ad_account_id=account_by_platform["google"].id,
        platform_id=google_platform.id,
        external_id="SIM-C-BRANDED-SEARCH",
        name="Google Ads branded search",
        objective="conversions",
        status="active",
        daily_budget=1600,
        metadata_json={
            "scenario_key": "shopping_brand_cannibalization",
            "simulator_mode": True,
            "campaign_role": "branded_search",
        },
    )
    session.add(brand_search)
    await session.flush()
    session.add(
        CampaignSkuMap(
            campaign_id=brand_search.id,
            product_id=shopping_product.id,
            weight=1,
            mapping_source="seeded_demo",
        )
    )
    for offset in range(30):
        observed = branded_search_observation(offset)
        observation_date = today - timedelta(days=29 - offset)
        daily_spend = observed["spend"]
        daily_revenue = observed["revenue"]
        clicks = observed["clicks"]
        impressions = observed["impressions"]
        conversions = observed["conversions"]
        units = max(1, round(conversions))
        order_revenue = observed["order_revenue"]
        session.add(
            AdMetricDaily(
                campaign_id=brand_search.id,
                metric_date=observation_date,
                spend=daily_spend,
                impressions=impressions,
                clicks=clicks,
                conversions=conversions,
                attributed_revenue=daily_revenue,
                source_payload={
                    "scenario_key": "shopping_brand_cannibalization",
                    "campaign_role": "branded_search",
                    "simulator": True,
                },
            )
        )
        session.add(
            Order(
                brand_id=brand.id,
                external_id=f"SIM-ORDER-BRAND-SEARCH-{observation_date.isoformat()}",
                ordered_at=datetime.combine(observation_date, time(14), timezone.utc),
                sku=shopping_product.sku,
                quantity=units,
                net_revenue=order_revenue,
                cogs=units * shopping_product.unit_cost,
                currency_code="USD",
                source="simulator",
            )
        )
        session.add(
            UnifiedFact(
                brand_id=brand.id,
                fact_date=observation_date,
                platform=google_platform.name,
                campaign_id=brand_search.id,
                product_id=shopping_product.id,
                spend=daily_spend,
                attributed_revenue=daily_revenue,
                reconciled_revenue=order_revenue,
                cogs=units * shopping_product.unit_cost,
                contribution_profit=order_revenue - units * shopping_product.unit_cost - daily_spend,
                clicks=clicks,
                impressions=impressions,
                conversions=conversions,
                reconciliation_factor=order_revenue / daily_revenue,
                source="simulator",
            )
        )

    session.add(
        BusinessObjective(
            brand_id=brand.id,
            name="Protect profitable growth",
            spend_floor=25000,
            spend_cap=45000,
            target_roas=2.5,
            max_shift_pct=20,
            objective="contribution_profit",
        )
    )
    session.add(
        SimulationState(
            brand_id=brand.id,
            simulated_date=today,
            cumulative_profit_uplift=0,
            advanced_days=0,
        )
    )
    session.add(
        ModelRegistry(
            name="adpilot_simulator_baseline",
            version="1.0.0",
            metrics={"scenario_count": 8, "observations": 240, "simulated_mape_pct": 0},
            parameters={"reconciliation": "proportional_order_revenue"},
        )
    )
    await session.flush()
    facts = list(
        (
            await session.scalars(
                select(UnifiedFact).where(UnifiedFact.brand_id == brand.id)
            )
        ).all()
    )
    orders = list(
        (
            await session.scalars(select(Order).where(Order.brand_id == brand.id))
        ).all()
    )
    product_id_by_sku = {product.sku: product.id for product in products_by_sku.values()}
    reported_by_product_day: dict[tuple[str, date], float] = {}
    actual_by_product_day: dict[tuple[str, date], float] = {}
    for fact in facts:
        if fact.product_id:
            key = (fact.product_id, fact.fact_date)
            reported_by_product_day[key] = (
                reported_by_product_day.get(key, 0.0) + fact.attributed_revenue
            )
    for order in orders:
        product_id = product_id_by_sku.get(order.sku)
        if product_id:
            key = (product_id, order.ordered_at.date())
            actual_by_product_day[key] = (
                actual_by_product_day.get(key, 0.0) + order.net_revenue
            )
    for fact in facts:
        if not fact.product_id:
            continue
        key = (fact.product_id, fact.fact_date)
        reported = reported_by_product_day.get(key, 0.0)
        actual = actual_by_product_day.get(key, 0.0)
        factor = min(1.0, actual / reported) if reported else 0.0
        fact.reconciliation_factor = factor
        fact.reconciled_revenue = fact.attributed_revenue * factor
        fact.contribution_profit = (
            fact.reconciled_revenue - fact.cogs - fact.discounts - fact.spend
        )
    await session.commit()
    return {"seeded": 8, "brand_id": brand.id}
