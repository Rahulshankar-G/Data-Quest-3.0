import os
import sqlite3
from pathlib import Path

from sqlalchemy import text

from src.adpilot.db import Base, engine
from src.adpilot import models  # noqa: F401


def _sqlite_path_for_url(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite"):
        return None
    if database_url.startswith("sqlite+aiosqlite:///"):
        path = database_url.split("sqlite+aiosqlite:///", 1)[1]
    elif database_url.startswith("sqlite:///"):
        path = database_url.split("sqlite:///", 1)[1]
    else:
        return None
    if path.startswith("./"):
        path = path[2:]
    return (Path.cwd() / path).resolve() if not Path(path).is_absolute() else Path(path)


def _is_valid_sqlite_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with sqlite3.connect(path) as connection:
            connection.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return True
    except sqlite3.DatabaseError:
        return False


async def _reset_invalid_sqlite_database() -> None:
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/adpilot.db")
    database_path = _sqlite_path_for_url(database_url)
    if database_path is None or not database_path.exists():
        return
    if _is_valid_sqlite_file(database_path):
        return
    try:
        database_path.unlink(missing_ok=True)
    except PermissionError:
        return
    database_path.parent.mkdir(parents=True, exist_ok=True)


async def _ensure_legacy_user_columns() -> None:
    async with engine.begin() as connection:
        result = await connection.execute(text("PRAGMA table_info(users)"))
        columns = {row[1] for row in result.fetchall()}
        if not columns:
            return

        if "username" not in columns:
            await connection.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR(80) DEFAULT ''"))
            await connection.execute(text(
                "UPDATE users SET username = CASE WHEN instr(email, '@') > 0 THEN substr(email, 1, instr(email, '@') - 1) ELSE email END WHERE username IS NULL OR username = ''"
            ))
        if "display_name" not in columns:
            await connection.execute(text("ALTER TABLE users ADD COLUMN display_name VARCHAR(160) DEFAULT ''"))
            await connection.execute(text(
                "UPDATE users SET display_name = CASE WHEN length(trim(username)) > 0 THEN username ELSE email END WHERE display_name IS NULL OR display_name = ''"
            ))
        if "brand_id" not in columns:
            await connection.execute(text("ALTER TABLE users ADD COLUMN brand_id VARCHAR(36) DEFAULT ''"))
        if "role_id" not in columns:
            await connection.execute(text("ALTER TABLE users ADD COLUMN role_id VARCHAR(36) DEFAULT ''"))
        if "active" not in columns:
            await connection.execute(text("ALTER TABLE users ADD COLUMN active BOOLEAN DEFAULT 1"))
        if "password_hash" not in columns:
            await connection.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255) DEFAULT ''"))
        if "created_at" not in columns:
            await connection.execute(text("ALTER TABLE users ADD COLUMN created_at DATETIME"))

        await connection.execute(text(
            "UPDATE users SET username = CASE WHEN length(trim(username)) > 0 THEN username ELSE CASE WHEN instr(email, '@') > 0 THEN substr(email, 1, instr(email, '@') - 1) ELSE email END END WHERE username IS NULL OR length(trim(username)) = 0"
        ))
        await connection.execute(text(
            "UPDATE users SET display_name = CASE WHEN length(trim(display_name)) > 0 THEN display_name ELSE CASE WHEN length(trim(username)) > 0 THEN username ELSE email END END WHERE display_name IS NULL OR length(trim(display_name)) = 0"
        ))


async def upgrade() -> None:
    await _reset_invalid_sqlite_database()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await _ensure_legacy_user_columns()


async def downgrade() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
