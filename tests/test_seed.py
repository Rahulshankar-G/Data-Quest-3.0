import unittest
from datetime import datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.adpilot.routes import (
    ApprovalRevocationInput,
    ClockAdvanceInput,
    OptimizerInput,
    advance_simulation_clock,
    budget_optimizer,
    execute_recommendation,
    learning,
    revoke_recommendation_approval,
)
from src.adpilot.connectors import CONNECTORS
from src.adpilot.jobs import ingest_simulator_data
from src.adpilot.models import (
    AdMetricDaily,
    Base,
    Brand,
    Campaign,
    Execution,
    Outcome,
    Recommendation,
    SimulationState,
    UnifiedFact,
)
from src.adpilot.seed import (
    BRAND_NAME,
    branded_search_observation,
    daily_observation,
    seed_demo,
)


class SeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_revoke_approval_returns_recommendation_to_proposed(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                seeded = await seed_demo(session)
                recommendation = Recommendation(
                    brand_id=seeded["brand_id"],
                    title="Approved test recommendation",
                    rationale="Regression test",
                    proposed_change_pct=10,
                    status="approved",
                    payload={
                        "approved_by": "Demo Operator",
                        "approved_at": "2026-10-08T00:00:00+00:00",
                    },
                )
                session.add(recommendation)
                await session.commit()

                result = await revoke_recommendation_approval(
                    recommendation.id,
                    ApprovalRevocationInput(revoked_by="Demo Operator"),
                    session,
                )

                self.assertEqual(result["status"], "proposed")
                self.assertNotIn("approved_by", result["payload"])
                self.assertNotIn("approved_at", result["payload"])
                self.assertEqual(
                    result["payload"]["approval_revoked_by"],
                    "Demo Operator",
                )
                with self.assertRaises(HTTPException) as execution_error:
                    await execute_recommendation(recommendation.id, session)
                self.assertEqual(execution_error.exception.status_code, 409)
        finally:
            await engine.dispose()

    async def test_clock_skips_occupied_metric_date_and_records_execution_outcome(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                await seed_demo(session)
                brand = await session.scalar(
                    select(Brand).where(Brand.name == BRAND_NAME)
                )
                state = await session.scalar(
                    select(SimulationState).where(
                        SimulationState.brand_id == brand.id
                    )
                )
                campaign = await session.scalar(
                    select(Campaign).where(
                        Campaign.name == "Meta creative fatigue"
                    )
                )
                other_campaign = await session.scalar(
                    select(Campaign).where(
                        Campaign.name == "TikTok rising marginal ROAS"
                    )
                )
                next_date = state.simulated_date + timedelta(days=1)
                occupied_metric = AdMetricDaily(
                    campaign_id=campaign.id,
                    metric_date=next_date,
                    spend=123,
                    impressions=1000,
                    clicks=25,
                    conversions=3,
                    attributed_revenue=246,
                    source_payload={"simulated": True, "source": "ingestion"},
                )
                recommendation = Recommendation(
                    brand_id=brand.id,
                    campaign_id=campaign.id,
                    title="Executed test recommendation",
                    rationale="Regression test",
                    proposed_change_pct=10,
                    status="executed",
                )
                other_recommendation = Recommendation(
                    brand_id=brand.id,
                    campaign_id=other_campaign.id,
                    title="Executed second campaign recommendation",
                    rationale="Regression test",
                    proposed_change_pct=10,
                    status="executed",
                )
                session.add_all([
                    occupied_metric,
                    recommendation,
                    other_recommendation,
                ])
                await session.flush()
                execution = Execution(
                    recommendation_id=recommendation.id,
                    mode="simulator",
                    status="executed",
                    before_state={"daily_budget": campaign.daily_budget},
                    after_state={"daily_budget": campaign.daily_budget * 1.1},
                )
                other_execution = Execution(
                    recommendation_id=other_recommendation.id,
                    mode="simulator",
                    status="executed",
                    before_state={"daily_budget": other_campaign.daily_budget},
                    after_state={"daily_budget": other_campaign.daily_budget * 1.1},
                )
                session.add_all([execution, other_execution])
                await session.commit()

                metric_selects = []

                def count_metric_selects(
                    _connection,
                    _cursor,
                    statement,
                    _parameters,
                    _context,
                    _many,
                ):
                    if (
                        "ad_metrics_daily" in statement.lower()
                        and statement.lstrip().lower().startswith("select")
                    ):
                        metric_selects.append(statement)

                event.listen(
                    engine.sync_engine,
                    "before_cursor_execute",
                    count_metric_selects,
                )
                result = await advance_simulation_clock(
                    ClockAdvanceInput(days=1),
                    session,
                )
                self.assertLessEqual(
                    len(metric_selects),
                    2,
                    "Clock advance should load metric dates and latest metrics in bulk.",
                )
                persisted_metric = await session.scalar(
                    select(AdMetricDaily).where(
                        AdMetricDaily.campaign_id == campaign.id,
                        AdMetricDaily.metric_date == next_date,
                    )
                )
                outcomes = list(
                    (
                        await session.scalars(
                            select(Outcome).where(
                                Outcome.execution_id.in_(
                                    [execution.id, other_execution.id]
                                )
                            )
                        )
                    ).all()
                )

                self.assertEqual(result["simulated_date"], next_date.isoformat())
                self.assertEqual(persisted_metric.id, occupied_metric.id)
                self.assertEqual(persisted_metric.spend, 123)
                self.assertEqual(len(outcomes), 2)
                campaign_by_execution = {
                    execution.id: campaign.id,
                    other_execution.id: other_campaign.id,
                }
                for outcome in outcomes:
                    self.assertEqual(
                        outcome.payload["campaign_id"],
                        campaign_by_execution[outcome.execution_id],
                    )
                    self.assertEqual(outcome.payload["scope"], "executed_campaign")
                    self.assertFalse(outcome.payload["forecast_error_available"])
                self.assertFalse(result["model_recalibrated"])
                self.assertIsNone(result["mape_pct"])

                session.add(
                    Outcome(
                        recommendation_id=recommendation.id,
                        actual_profit=100,
                        counterfactual_profit=0,
                        payload={"scope": "portfolio_clock_legacy"},
                    )
                )
                await session.commit()
                await advance_simulation_clock(ClockAdvanceInput(days=1), session)
                outcomes = list(
                    (
                        await session.scalars(
                            select(Outcome).where(
                                Outcome.execution_id.in_(
                                    [execution.id, other_execution.id]
                                )
                            )
                        )
                    ).all()
                )
                self.assertEqual(len(outcomes), 2)
                learning_summary = await learning(session)
                self.assertEqual(learning_summary["outcome_count"], 2)
                self.assertEqual(learning_summary["forecast_error_count"], 0)
                self.assertIsNone(
                    learning_summary["mean_absolute_percentage_error"]
                )
        finally:
            await engine.dispose()

    async def test_clock_advances_the_maximum_window_and_persists_each_day(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                seeded = await seed_demo(session)
                brand = await session.get(Brand, seeded["brand_id"])
                state = await session.scalar(
                    select(SimulationState).where(
                        SimulationState.brand_id == brand.id
                    )
                )
                start_date = state.simulated_date
                campaign_count = await session.scalar(
                    select(func.count())
                    .select_from(Campaign)
                    .where(Campaign.brand_id == brand.id)
                )

                result = await advance_simulation_clock(
                    ClockAdvanceInput(days=30),
                    session,
                )

                self.assertEqual(result["advanced_days"], 30)
                self.assertEqual(
                    result["simulated_date"],
                    (start_date + timedelta(days=30)).isoformat(),
                )
                simulated_fact_count = await session.scalar(
                    select(func.count())
                    .select_from(UnifiedFact)
                    .where(
                        UnifiedFact.brand_id == brand.id,
                        UnifiedFact.source == "simulator_clock",
                        UnifiedFact.fact_date > start_date,
                    )
                )
                self.assertEqual(simulated_fact_count, campaign_count * 30)
        finally:
            await engine.dispose()

    async def test_clock_uses_recent_weekday_metrics_instead_of_repeating_last_day(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                seeded = await seed_demo(session)
                brand = await session.get(Brand, seeded["brand_id"])
                state = await session.scalar(
                    select(SimulationState).where(
                        SimulationState.brand_id == brand.id
                    )
                )
                campaign = await session.scalar(
                    select(Campaign).where(
                        Campaign.name == "Meta creative fatigue"
                    )
                )
                first_date = state.simulated_date + timedelta(days=1)

                await advance_simulation_clock(
                    ClockAdvanceInput(days=7),
                    session,
                )

                future_facts = list(
                    (
                        await session.scalars(
                            select(UnifiedFact)
                            .where(
                                UnifiedFact.campaign_id == campaign.id,
                                UnifiedFact.source == "simulator_clock",
                                UnifiedFact.fact_date >= first_date,
                            )
                            .order_by(UnifiedFact.fact_date)
                        )
                    ).all()
                )
                self.assertEqual(len(future_facts), 7)
                self.assertGreater(
                    len({round(fact.spend, 2) for fact in future_facts}),
                    1,
                )
                self.assertGreater(
                    len({round(fact.contribution_profit, 2) for fact in future_facts}),
                    1,
                )
        finally:
            await engine.dispose()

    async def test_optimizer_uses_daily_budget_and_observed_revenue_window(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                seeded = await seed_demo(session)
                campaigns = list(
                    (
                        await session.scalars(
                            select(Campaign).where(
                                Campaign.brand_id == seeded["brand_id"]
                            )
                        )
                    ).all()
                )
                daily_budget = sum(item.daily_budget for item in campaigns)

                result = await budget_optimizer(
                    OptimizerInput(
                        total_budget=daily_budget,
                        max_shift_pct=20,
                        target_roas=0,
                    ),
                    session,
                )

                self.assertEqual(result["budget_period"], "daily")
                self.assertEqual(result["data_window"]["days"], 30)
                self.assertTrue(result["converged"])
                self.assertAlmostEqual(result["budget_optimized"], daily_budget, places=2)
                self.assertTrue(result["response_curves"])
                self.assertTrue(
                    all(
                        curve["fit_method"] == "fitted_to_daily_revenue"
                        for curve in result["response_curves"].values()
                    )
                )
        finally:
            await engine.dispose()

    async def test_seed_on_existing_brand_preserves_imported_facts(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                seeded = await seed_demo(session)
                imported_fact = UnifiedFact(
                    brand_id=seeded["brand_id"],
                    fact_date=datetime.now(timezone.utc).date(),
                    platform="CSV Import",
                    spend=12.5,
                    attributed_revenue=30,
                    reconciled_revenue=30,
                    contribution_profit=17.5,
                    source="csv_upload",
                )
                session.add(imported_fact)
                await session.commit()

                existing_campaign_count = await session.scalar(
                    select(func.count()).select_from(Campaign)
                )
                result = await seed_demo(session)
                preserved_fact = await session.scalar(
                    select(UnifiedFact).where(UnifiedFact.id == imported_fact.id)
                )
                campaign_count = await session.scalar(
                    select(func.count()).select_from(Campaign)
                )

                self.assertEqual(result["seeded"], 0)
                self.assertEqual(result["brand_id"], seeded["brand_id"])
                self.assertIsNotNone(preserved_fact)
                self.assertEqual(campaign_count, existing_campaign_count)
        finally:
            await engine.dispose()

    async def test_seeded_daily_metrics_use_the_same_units_as_simulator_ingestion(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                await seed_demo(session)
                campaign = await session.scalar(
                    select(Campaign).where(
                        Campaign.name == "Meta creative fatigue"
                    )
                )
                metric = await session.scalar(
                    select(AdMetricDaily)
                    .where(AdMetricDaily.campaign_id == campaign.id)
                    .order_by(AdMetricDaily.metric_date.desc())
                )

                expected = daily_observation(
                    CONNECTORS["meta"].baseline,
                    "meta_creative_fatigue",
                    29,
                )

                self.assertEqual(
                    metric.metric_date,
                    datetime.now(timezone.utc).date(),
                )
                self.assertAlmostEqual(metric.spend, expected["spend"])
                self.assertAlmostEqual(
                    metric.attributed_revenue, expected["revenue"]
                )
                self.assertEqual(metric.impressions, round(expected["impressions"]))
                self.assertEqual(metric.clicks, round(expected["clicks"]))
                self.assertAlmostEqual(metric.conversions, expected["conversions"])
        finally:
            await engine.dispose()

    async def test_startup_ingestion_preserves_seeded_scenario_daily_values(self):
        engine = create_async_engine("sqlite+aiosqlite://")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as session:
                await seed_demo(session)
                brand = await session.scalar(
                    select(Brand).where(Brand.name == BRAND_NAME)
                )
                meta_campaign = await session.scalar(
                    select(Campaign).where(
                        Campaign.name == "Meta creative fatigue"
                    )
                )
                branded_search = await session.scalar(
                    select(Campaign).where(
                        Campaign.name == "Google Ads branded search"
                    )
                )
                today = datetime.now(timezone.utc).date()
                await ingest_simulator_data(
                    session,
                    brand,
                    datetime.combine(today, time(12), timezone.utc),
                )

                meta_metric = await session.scalar(
                    select(AdMetricDaily).where(
                        AdMetricDaily.campaign_id == meta_campaign.id,
                        AdMetricDaily.metric_date == today,
                    )
                )
                branded_metric = await session.scalar(
                    select(AdMetricDaily).where(
                        AdMetricDaily.campaign_id == branded_search.id,
                        AdMetricDaily.metric_date == today,
                    )
                )

                expected_meta = daily_observation(
                    CONNECTORS["meta"].baseline,
                    "meta_creative_fatigue",
                    29,
                )
                expected_branded = branded_search_observation(29)
                self.assertAlmostEqual(meta_metric.spend, expected_meta["spend"])
                self.assertAlmostEqual(
                    meta_metric.attributed_revenue,
                    expected_meta["revenue"],
                )
                self.assertAlmostEqual(
                    branded_metric.spend,
                    expected_branded["spend"],
                )
                self.assertAlmostEqual(
                    branded_metric.attributed_revenue,
                    expected_branded["revenue"],
                )
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
