import io
import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.datastructures import UploadFile

from src.adpilot.db import Base
from src.adpilot.models import AdMetricDaily, Brand, Platform, UnifiedFact
from src.adpilot.routes import _source_health_payload, source_health, upload_data
from src.adpilot.seed import BRAND_NAME


class DataHubImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            session.add(Brand(name=BRAND_NAME))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_upload_appends_duplicate_rows_across_repeated_imports(self):
        content = (
            "date,campaign,spend,revenue\n"
            "2026-10-01,Campaign A,12.5,30\n"
            "2026-10-01,Campaign A,12.5,30\n"
            "2026-10-01,Campaign B,15,40\n"
        ).encode()

        async with self.sessions() as session:
            first = await upload_data(
                UploadFile(filename="campaigns.csv", file=io.BytesIO(content)),
                "{}",
                session,
            )
            second = await upload_data(
                UploadFile(filename="campaigns.csv", file=io.BytesIO(content)),
                "{}",
                session,
            )

            facts = list((await session.scalars(select(UnifiedFact))).all())
            metrics = list((await session.scalars(select(AdMetricDaily))).all())
            sources = await source_health(session)

        self.assertEqual(first["accepted_records"], 3)
        self.assertEqual(first["total_records"], 3)
        self.assertEqual(second["accepted_records"], 3)
        self.assertEqual(len(facts), 6)
        self.assertEqual(len(metrics), 6)
        simulator = next(source for source in sources if source["key"] == "simulator")
        self.assertEqual(simulator["health_score"], 100)
        self.assertEqual(simulator["record_counts"]["facts"], 6)

    def test_health_is_accepted_attribute_values_over_values_given(self):
        platform = Platform(key="meta", name="Meta")
        valid = UnifiedFact(
            fact_date="2026-10-01",
            platform="Meta",
            spend=10,
            attributed_revenue=20,
            reconciled_revenue=20,
            impressions=100,
            clicks=5,
            conversions=2,
        )
        invalid_spend = UnifiedFact(
            fact_date="2026-10-02",
            platform="Meta",
            spend=-1,
            attributed_revenue=20,
            reconciled_revenue=20,
            impressions=100,
            clicks=5,
            conversions=2,
        )
        result = _source_health_payload(
            "meta",
            platform,
            [valid, invalid_spend],
            1,
            0,
            0,
            {
                "date": lambda row: row.fact_date is not None,
                "spend": lambda row: row.spend >= 0,
                "revenue": lambda row: row.reconciled_revenue >= 0,
            },
        )

        self.assertEqual(result["attribute_health"]["spend"]["accepted"], 1)
        self.assertEqual(result["attribute_health"]["spend"]["total"], 2)
        self.assertEqual(result["attribute_health"]["spend"]["health_score"], 50)
        self.assertAlmostEqual(result["health_score"], 83.33, places=2)


if __name__ == "__main__":
    unittest.main()
