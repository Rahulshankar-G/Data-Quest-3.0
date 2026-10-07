import unittest
from datetime import datetime, time, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.adpilot.connectors import CONNECTORS
from src.adpilot.jobs import ingest_simulator_data
from src.adpilot.models import AdMetricDaily, Base, Brand, Campaign
from src.adpilot.seed import (
    BRAND_NAME,
    branded_search_observation,
    daily_observation,
    seed_demo,
)


class SeedTests(unittest.IsolatedAsyncioTestCase):
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
