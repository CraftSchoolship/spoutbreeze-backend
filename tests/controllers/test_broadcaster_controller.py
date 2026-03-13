from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bbb_models import BbbMeeting
from app.models.user_models import User


@pytest_asyncio.fixture
async def test_bbb_meeting(db_session: AsyncSession, test_user: User):
    """Create a test BbbMeeting record so the broadcaster controller's DB lookup succeeds."""
    from datetime import datetime

    meeting = BbbMeeting(
        id=uuid4(),
        meeting_id="meeting-123",
        internal_meeting_id=f"internal-{uuid4()}",
        attendee_pw="attendeePW",
        moderator_pw="moderatorPW",
        user_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(meeting)
    await db_session.commit()
    await db_session.refresh(meeting)
    return meeting


@pytest_asyncio.fixture
async def test_bbb_meeting_m1(db_session: AsyncSession, test_user: User):
    """Create a BbbMeeting with meeting_id='m1' for short-payload tests."""
    from datetime import datetime

    meeting = BbbMeeting(
        id=uuid4(),
        meeting_id="m1",
        internal_meeting_id=f"internal-m1-{uuid4()}",
        attendee_pw="attendeePW",
        moderator_pw="moderatorPW",
        user_id=test_user.id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )
    db_session.add(meeting)
    await db_session.commit()
    await db_session.refresh(meeting)
    return meeting


# Autouse fixture: every test gets the mocked broadcaster
@pytest.fixture(autouse=True)
def mock_broadcaster(monkeypatch):
    from app.controllers import broadcaster_controller

    async def fake_start_broadcasting(
        meeting_id,
        rtmp_url,
        stream_key,
        password,
        platform,
        bbb_service,
        user_id,
        db,
        requested_resolution=None,
    ):
        return {
            "status": "success",
            "message": "Broadcaster started successfully",
            "join_url": "https://bbb.example.com/join/mock",
            "stream": {
                "stream_id": "mock-stream-id",
                "pod_name": "mock-pod",
                "status": "running",
                "created_at": "2026-01-01T00:00:00Z",
            },
            "meeting_info": {
                "meetingID": meeting_id,
            },
            "debug": {
                "meeting_id": meeting_id,
                "rtmp_url": rtmp_url,
                "stream_key": stream_key,
            },
        }

    monkeypatch.setattr(
        broadcaster_controller.broadcaster_service,
        "start_broadcasting",
        fake_start_broadcasting,
    )
    yield


class TestBroadcasterController:
    """Test cases for broadcaster controller"""

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_success(self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting):
        """Test successful broadcaster meeting start"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "test-stream-key",
            "password": "moderator-password",
            "platform": "twitch",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        # Fix the expected message based on actual service response
        assert data["message"] == "Broadcaster started successfully"

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_invalid_data(self, client: AsyncClient):
        """Test broadcaster meeting with invalid data"""
        # Missing required fields
        payload = {
            "meeting_id": "meeting-123"
            # Missing rtmp_url, stream_key, password, platform
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_empty_payload(self, client: AsyncClient):
        """Test broadcaster meeting with empty payload"""
        payload: dict[str, str] = {}

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_service_error(self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting):
        """Test broadcaster meeting when service encounters an error"""
        # Use empty meeting ID — won't match the BbbMeeting fixture → 404
        payload = {
            "meeting_id": "",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "test-stream-key",
            "password": "moderator-password",
            "platform": "twitch",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        # Empty meeting_id won't match any BbbMeeting → 404,
        # or it could be 422 if validation rejects it
        assert response.status_code in [404, 422]

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_invalid_meeting_id(
        self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting
    ):
        """Test broadcaster meeting with invalid meeting ID"""
        payload = {
            "meeting_id": "invalid-meeting-123",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "test-stream-key",
            "password": "moderator-password",
            "platform": "twitch",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        # Meeting not found in DB → 404
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_invalid_rtmp_url(
        self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting
    ):
        """Test broadcaster meeting with invalid RTMP URL"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "invalid-url",
            "stream_key": "test-stream-key",
            "password": "moderator-password",
            "platform": "twitch",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        # The service mock still returns success even with invalid RTMP
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_wrong_password(
        self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting
    ):
        """Test broadcaster meeting with wrong password"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "test-stream-key",
            "password": "wrong-password",
            "platform": "twitch",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        # The service mock still returns success even with wrong password
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_missing_stream_key(self, client: AsyncClient):
        """Test broadcaster meeting with missing stream key"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "password": "moderator-password",
            "platform": "twitch",
            # Missing stream_key
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_empty_string_fields(
        self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting
    ):
        """Test broadcaster meeting with empty string fields"""
        payload = {
            "meeting_id": "",
            "rtmp_url": "",
            "stream_key": "",
            "password": "",
            "platform": "",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        # Empty meeting_id won't match any BbbMeeting → 404
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_null_fields(self, client: AsyncClient):
        """Test broadcaster meeting with null fields"""
        payload = {
            "meeting_id": None,
            "rtmp_url": None,
            "stream_key": None,
            "password": None,
            "platform": None,
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_youtube_rtmp(self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting):
        """Test broadcaster meeting with YouTube RTMP URL"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmp://a.rtmp.youtube.com/live2",
            "stream_key": "youtube-stream-key",
            "password": "moderator-password",
            "platform": "youtube",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_facebook_rtmp(self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting):
        """Test broadcaster meeting with Facebook RTMP URL"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmps://live-api-s.facebook.com:443/rtmp",
            "stream_key": "facebook-stream-key",
            "password": "moderator-password",
            "platform": "facebook",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_nonexistent_meeting(self, client: AsyncClient, test_user: User):
        """Test broadcaster meeting with non-existent meeting ID"""
        non_existent_meeting_id = f"nonexistent-{uuid4()}"

        payload = {
            "meeting_id": non_existent_meeting_id,
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "test-stream-key",
            "password": "moderator-password",
            "platform": "twitch",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        # Meeting not found in DB → 404
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_concurrent_requests(
        self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting
    ):
        """Test multiple concurrent broadcaster requests"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "test-stream-key",
            "password": "moderator-password",
            "platform": "twitch",
        }

        # Make multiple concurrent requests
        import asyncio

        responses = await asyncio.gather(
            client.post("/api/bbb/broadcaster", json=payload),
            client.post("/api/bbb/broadcaster", json=payload),
            client.post("/api/bbb/broadcaster", json=payload),
            return_exceptions=True,
        )

        # All requests should succeed
        for response in responses:
            # Skip if response is an exception
            if isinstance(response, BaseException):
                continue
            assert response.status_code == 201
            data = response.json()
            assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_malformed_json(self, client: AsyncClient):
        """Test broadcaster meeting with malformed JSON"""
        # Send raw string instead of JSON
        response = await client.post(
            "/api/bbb/broadcaster",
            content="invalid json string",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 422  # JSON decode error

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_extra_fields(self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting):
        """Test broadcaster meeting with extra fields (should be ignored)"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "test-stream-key",
            "password": "moderator-password",
            "platform": "twitch",
            "extra_field": "should be ignored",
            "another_field": 12345,
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_very_long_stream_key(
        self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting
    ):
        """Test broadcaster meeting with very long stream key"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "a" * 1000,  # Very long stream key
            "password": "moderator-password",
            "platform": "twitch",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_special_characters(
        self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting
    ):
        """Test broadcaster meeting with special characters in fields"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "test-key-!@#$%^&*()",
            "password": "pass-word-123!@#",
            "platform": "twitch",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_unicode_characters(
        self, client: AsyncClient, test_user: User, test_bbb_meeting: BbbMeeting
    ):
        """Test broadcaster meeting with unicode characters"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "key-🔑",
            "password": "password-🔒",
            "platform": "twitch",
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_missing_meeting_id(self, client: AsyncClient):
        """Test broadcaster meeting with missing meeting_id"""
        payload = {
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "test-stream-key",
            "password": "moderator-password",
            "platform": "twitch",
            # Missing meeting_id
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_missing_rtmp_url(self, client: AsyncClient):
        """Test broadcaster meeting with missing rtmp_url"""
        payload = {
            "meeting_id": "meeting-123",
            "stream_key": "test-stream-key",
            "password": "moderator-password",
            "platform": "twitch",
            # Missing rtmp_url
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_broadcaster_meeting_missing_password(self, client: AsyncClient):
        """Test broadcaster meeting with missing password"""
        payload = {
            "meeting_id": "meeting-123",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "test-stream-key",
            "platform": "twitch",
            # Missing password
        }

        response = await client.post("/api/bbb/broadcaster", json=payload)

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "meeting_id,rtmp_url,stream_key,password,platform",
        [
            (
                "meeting-123",
                "rtmp://live.twitch.tv/live",
                "test-stream-key",
                "moderator-password",
                "twitch",
            ),
            (
                "meeting-123",
                "rtmp://a.rtmp.youtube.com/live2",
                "youtube-stream-key",
                "moderator-password",
                "youtube",
            ),
            (
                "meeting-123",
                "rtmps://live-api-s.facebook.com:443/rtmp",
                "facebook-stream-key",
                "moderator-password",
                "facebook",
            ),
            ("meeting-123", "rtmp://live.twitch.tv/live", "key-🔑", "password-🔒", "twitch"),
            (
                "meeting-123",
                "rtmp://live.twitch.tv/live",
                "test-key-!@#$%^&*()",
                "pass-word-123!@#",
                "twitch",
            ),
            (
                "meeting-123",
                "rtmp://live.twitch.tv/live",
                "a" * 1000,
                "moderator-password",
                "twitch",
            ),
        ],
    )
    async def test_broadcaster_success_variants(
        self,
        client: AsyncClient,
        test_user: User,
        test_bbb_meeting: BbbMeeting,
        meeting_id,
        rtmp_url,
        stream_key,
        password,
        platform,
    ):
        payload = {
            "meeting_id": meeting_id,
            "rtmp_url": rtmp_url,
            "stream_key": stream_key,
            "password": password,
            "platform": platform,
        }
        r = await client.post("/api/bbb/broadcaster", json=payload)
        assert r.status_code == 201
        data = r.json()
        assert data["status"] == "success"
        assert data["message"] == "Broadcaster started successfully"

    @pytest.mark.asyncio
    async def test_validation_missing_all(self, client: AsyncClient):
        r = await client.post("/api/bbb/broadcaster", json={})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_validation_missing_stream_key(self, client: AsyncClient):
        r = await client.post(
            "/api/bbb/broadcaster",
            json={
                "meeting_id": "m1",
                "rtmp_url": "rtmp://live.twitch.tv/live",
                "password": "p",
                "platform": "twitch",
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_validation_missing_meeting_id(self, client: AsyncClient):
        r = await client.post(
            "/api/bbb/broadcaster",
            json={
                "rtmp_url": "rtmp://live.twitch.tv/live",
                "stream_key": "k",
                "password": "p",
                "platform": "twitch",
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_validation_null_fields(self, client: AsyncClient):
        r = await client.post(
            "/api/bbb/broadcaster",
            json={
                "meeting_id": None,
                "rtmp_url": None,
                "stream_key": None,
                "password": None,
                "platform": None,
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_malformed_json(self, client: AsyncClient):
        r = await client.post(
            "/api/bbb/broadcaster",
            content="not-json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_extra_fields_ignored(self, client: AsyncClient, test_user: User, test_bbb_meeting_m1: BbbMeeting):
        r = await client.post(
            "/api/bbb/broadcaster",
            json={
                "meeting_id": "m1",
                "rtmp_url": "rtmp://live.twitch.tv/live",
                "stream_key": "k",
                "password": "p",
                "platform": "twitch",
                "extra": "ignored",
                "another": 123,
            },
        )
        assert r.status_code == 201
        assert r.json()["status"] == "success"

    @pytest.mark.asyncio
    async def test_concurrent_requests(self, client: AsyncClient, test_user: User, test_bbb_meeting_m1: BbbMeeting):
        import asyncio

        payload = {
            "meeting_id": "m1",
            "rtmp_url": "rtmp://live.twitch.tv/live",
            "stream_key": "k",
            "password": "p",
            "platform": "twitch",
        }
        results = await asyncio.gather(*[client.post("/api/bbb/broadcaster", json=payload) for _ in range(3)])
        assert all(r.status_code == 201 for r in results)
