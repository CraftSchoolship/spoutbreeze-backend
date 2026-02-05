import pytest
import pytest_asyncio
import asyncio
import sys
from unittest.mock import MagicMock

# Mock Keycloak BEFORE importing anything else to prevent connection errors
# Create mock objects with all required methods
mock_keycloak_openid = MagicMock()
mock_keycloak_openid.public_key.return_value = "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtestkey\n-----END PUBLIC KEY-----"
mock_keycloak_openid.well_known.return_value = {
    "issuer": "http://localhost:8080/realms/test",
    "authorization_endpoint": "http://localhost:8080/realms/test/protocol/openid-connect/auth",
    "token_endpoint": "http://localhost:8080/realms/test/protocol/openid-connect/token",
    "userinfo_endpoint": "http://localhost:8080/realms/test/protocol/openid-connect/userinfo",
    "end_session_endpoint": "http://localhost:8080/realms/test/protocol/openid-connect/logout",
    "jwks_uri": "http://localhost:8080/realms/test/protocol/openid-connect/certs",
}

mock_keycloak_admin = MagicMock()

# Mock the keycloak module's classes before they're imported by settings.py
# This creates a fake keycloak module with mocked classes
mock_keycloak_module = MagicMock()
mock_keycloak_module.KeycloakOpenID = MagicMock(return_value=mock_keycloak_openid)
mock_keycloak_module.KeycloakAdmin = MagicMock(return_value=mock_keycloak_admin)
sys.modules['keycloak'] = mock_keycloak_module

# These imports must come AFTER the keycloak mock to prevent connection errors
from httpx import AsyncClient, ASGITransport  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker  # noqa: E402
from uuid import uuid4  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

from app.main import app  # noqa: E402
from app.config.database.session import get_db, Base  # noqa: E402
from app.models.user_models import User  # noqa: E402
from app.models.channel.channels_model import Channel  # noqa: E402
from app.models.stream_models import RtmpEndpoint  # noqa: E402
from app.models.event.event_models import Event  # noqa: E402
from app.models.event.event_models import EventStatus  # noqa: E402
from app.models.payment_models import Subscription, Transaction  # noqa: E402

# Test database URL (SQLite for simplicity in tests)
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
            await session.execute(Transaction.__table__.delete())
            await session.execute(Subscription.__table__.delete())
            await session.execute(Event.__table__.delete())
            await session.execute(RtmpEndpoint.__table__.delete())
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
    stream_settings = RtmpEndpoint(
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


@pytest_asyncio.fixture
async def test_event(db_session: AsyncSession, test_user: User, test_channel: Channel):
    """Create a test event"""
    future_date = datetime.now() + timedelta(hours=1)
    event = Event(
        id=uuid4(),
        title=f"Test Event {uuid4()}",
        description="Test event description",
        occurs="once",
        start_date=future_date.date(),
        end_date=future_date.date(),
        start_time=future_date,
        timezone="UTC",
        channel_id=test_channel.id,
        creator_id=test_user.id,
        meeting_created=False,
        status=EventStatus.SCHEDULED,  # Use the enum value directly
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(event)
    await db_session.commit()
    await db_session.refresh(event)
    return event


@pytest.fixture
def mock_current_user(test_user: User):
    """Mock the get_current_user dependency"""

    def _mock_current_user():
        return test_user

    return _mock_current_user


@pytest.fixture
def anyio_backend():
    # Force AnyIO to use asyncio so Trio is not required
    return "asyncio"
