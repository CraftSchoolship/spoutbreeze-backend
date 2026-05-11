from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config.settings import Settings, get_settings


def build_engine_kwargs(s: Settings, db_url: str) -> dict:
    """Resolve the kwargs for ``create_async_engine``.

    Extracted so the policy ("echo gated on a setting; pool args only on
    non-sqlite URLs") is unit-testable without spinning up a real engine.

    `echo=True` flushes every SQL statement (with parameter values) to
    stdout. That inflates log volume, leaks user data via query parameters,
    and adds non-trivial latency. Off by default; opt in with DB_ECHO=true.

    SQLAlchemy's default async pool (AsyncAdaptedQueuePool) accepts
    pool_size and max_overflow; SQLite uses StaticPool, which doesn't.
    """
    kwargs: dict = {
        "echo": s.db_echo,
        "pool_pre_ping": s.db_pool_pre_ping,
    }
    if not db_url.startswith("sqlite"):
        kwargs["pool_size"] = s.db_pool_size
        kwargs["max_overflow"] = s.db_max_overflow
    return kwargs


settings = get_settings()
DATABASE_URL = settings.db_url
engine = create_async_engine(DATABASE_URL, **build_engine_kwargs(settings, DATABASE_URL))
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


async def get_db():
    """
    Dependency to get the database session.
    """
    async with SessionLocal() as db:
        try:
            yield db
        finally:
            await db.close()
