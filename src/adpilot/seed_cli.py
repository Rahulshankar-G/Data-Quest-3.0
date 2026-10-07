import asyncio

from src.adpilot.db import SessionLocal, engine
from src.adpilot.migrations import upgrade
from src.adpilot.seed import seed_demo


async def main() -> None:
    await upgrade()
    async with SessionLocal() as session:
        result = await seed_demo(session)
    print(f"AdPilot seed complete: {result}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
