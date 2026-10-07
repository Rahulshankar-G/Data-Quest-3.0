import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./data/adpilot-local-run.db",
)
engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    **({"pool_size": 5, "max_overflow": 10} if DATABASE_URL.startswith("postgresql+") else {}),
)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session():
    async with SessionLocal() as session:
        yield session
