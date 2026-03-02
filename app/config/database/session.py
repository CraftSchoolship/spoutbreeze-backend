from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config.settings import get_settings

settings = get_settings()

# Initialize the database engine
DATABASE_URL = settings.db_url

engine = create_async_engine(DATABASE_URL, echo=True)
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
