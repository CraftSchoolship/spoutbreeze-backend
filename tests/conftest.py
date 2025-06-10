import pytest
import pytest_asyncio
import asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from uuid import uuid4
from datetime import datetime

from app.main import app
from app.config.database.session import get_db, Base
from app.models.user_models import User
from app.models.channel.channels_model import Channel
from app.models.stream_models import StreamSettings


# Test database URL (use SQLite for simplicity in tests)
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

# Create test engine
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def override_get_db():
    """Override the get_db dependency for testing"""
    async with TestingSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def setup_database():
    """Create tables for testing"""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(setup_database):
    """Create a fresh database session for each test"""
    async with TestingSessionLocal() as session:
        try:
            yield session
        finally:
            # Clean up database after each test
            await session.rollback()
            # Delete all data from tables to ensure clean state
            await session.execute(StreamSettings.__table__.delete())
            await session.execute(Channel.__table__.delete())
            await session.execute(User.__table__.delete())
            await session.commit()


@pytest_asyncio.fixture
async def client(db_session):
    """Create a test client with database dependency override"""

    def override_get_db_sync():
        return db_session

    app.dependency_overrides[get_db] = override_get_db_sync

    # Use ASGITransport to properly connect AsyncClient with FastAPI app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession):
    """Create a test user"""
    user = User(
        id=uuid4(),
        keycloak_id=f"test-keycloak-id-{uuid4()}",
        username=f"testuser-{uuid4()}",
        email=f"test-{uuid4()}@example.com",
        first_name="Test",
        last_name="User",
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_channel(db_session: AsyncSession, test_user: User):
    """Create a test channel"""
    channel = Channel(
        id=uuid4(),
        name=f"Test Channel {uuid4()}",  # Make unique
        creator_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(channel)
    await db_session.commit()
    await db_session.refresh(channel)
    return channel


@pytest_asyncio.fixture
async def test_stream_settings(db_session: AsyncSession, test_user: User):
    """Create test stream settings"""
    stream_settings = StreamSettings(
        id=uuid4(),
        title=f"Test Stream {uuid4()}",
        stream_key=f"test-key-{uuid4()}",
        rtmp_url="rtmp://test.example.com/live",
        user_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(stream_settings)
    await db_session.commit()
    await db_session.refresh(stream_settings)
    return stream_settings


@pytest.fixture
def mock_current_user(test_user: User):
    """Mock the get_current_user dependency"""

    def _mock_current_user():
        return test_user

    return _mock_current_user
