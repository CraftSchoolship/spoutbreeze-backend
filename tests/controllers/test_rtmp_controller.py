import pytest
from httpx import AsyncClient
from uuid import uuid4

from app.main import app
from app.controllers.user_controller import get_current_user
from app.models.user_models import User
from app.models.stream_models import RtmpEndpoint


class TestRtmpController:
    """Test cases for RTMP controller"""

    @pytest.mark.asyncio
    async def test_create_rtmp_endpoint_success(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test successful rtmp endpoint creation"""
        app.dependency_overrides[get_current_user] = mock_current_user

        stream_data = {
            "title": "Test Stream",
            "stream_key": "test-stream-key",
            "rtmp_url": "rtmp://test.example.com/live",
        }

        response = await client.post("/api/stream-endpoint/create", json=stream_data)

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Test Stream"
        assert data["stream_key"] == "test-stream-key"
        assert data["rtmp_url"] == "rtmp://test.example.com/live"
        assert data["user_id"] == str(test_user.id)
        assert data["user_first_name"] == test_user.first_name
        assert data["user_last_name"] == test_user.last_name
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    @pytest.mark.asyncio
    async def test_create_rtmp_endpoint_invalid_data(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test creating rtmp endpoint with invalid data"""
        app.dependency_overrides[get_current_user] = mock_current_user

        # Missing required fields
        stream_data = {
            "title": "Test Stream"
            # Missing stream_key and rtmp_url
        }

        response = await client.post("/api/stream-endpoint/create", json=stream_data)

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_get_rtmp_endpoints_by_user(
        self,
        client: AsyncClient,
        test_user: User,
        test_stream_settings: RtmpEndpoint,
        mock_current_user,
    ):
        """Test getting rtmp endpoints for current user"""
        app.dependency_overrides[get_current_user] = mock_current_user

        response = await client.get("/api/stream-endpoint/")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == str(test_stream_settings.id)
        assert data[0]["title"] == test_stream_settings.title
        assert data[0]["stream_key"] == test_stream_settings.stream_key
        assert data[0]["user_id"] == str(test_user.id)

    @pytest.mark.asyncio
    async def test_get_rtmp_endpoints_by_user_no_endpoints(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test getting rtmp endpoints when user has no endpoints"""
        app.dependency_overrides[get_current_user] = mock_current_user

        response = await client.get("/api/stream-endpoint/")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    @pytest.mark.asyncio
    async def test_get_rtmp_endpoint_by_id_success(
        self,
        client: AsyncClient,
        test_user: User,
        test_stream_settings: RtmpEndpoint,
        mock_current_user,
    ):
        """Test getting rtmp endpoint by ID"""
        app.dependency_overrides[get_current_user] = mock_current_user

        response = await client.get(f"/api/stream-endpoint/{test_stream_settings.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(test_stream_settings.id)
        assert data["title"] == test_stream_settings.title
        assert data["stream_key"] == test_stream_settings.stream_key
        assert data["rtmp_url"] == test_stream_settings.rtmp_url
        assert data["user_id"] == str(test_user.id)

    @pytest.mark.asyncio
    async def test_get_rtmp_endpoint_by_id_not_found(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test getting non-existent rtmp endpoint"""
        app.dependency_overrides[get_current_user] = mock_current_user

        non_existent_id = uuid4()
        response = await client.get(f"/api/stream-endpoint/{non_existent_id}")

        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "Stream settings not found"

    @pytest.mark.asyncio
    async def test_update_rtmp_endpoint_success(
        self,
        client: AsyncClient,
        test_user: User,
        test_stream_settings: RtmpEndpoint,
        mock_current_user,
    ):
        """Test successful rtmp endpoint update"""
        app.dependency_overrides[get_current_user] = mock_current_user

        update_data = {
            "title": "Updated Stream Title",
            "rtmp_url": "rtmp://updated.example.com/live",
        }

        response = await client.put(
            f"/api/stream-endpoint/{test_stream_settings.id}", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated Stream Title"
        assert data["rtmp_url"] == "rtmp://updated.example.com/live"
        assert data["stream_key"] == test_stream_settings.stream_key  # Unchanged
        assert data["id"] == str(test_stream_settings.id)

    @pytest.mark.asyncio
    async def test_update_rtmp_endpoint_partial(
        self,
        client: AsyncClient,
        test_user: User,
        test_stream_settings: RtmpEndpoint,
        mock_current_user,
    ):
        """Test partial update of rtmp endpoint"""
        app.dependency_overrides[get_current_user] = mock_current_user

        update_data = {
            "title": "Partially Updated Title"
            # Only updating title, leaving other fields unchanged
        }

        response = await client.put(
            f"/api/stream-endpoint/{test_stream_settings.id}", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Partially Updated Title"
        assert data["stream_key"] == test_stream_settings.stream_key  # Unchanged
        assert data["rtmp_url"] == test_stream_settings.rtmp_url  # Unchanged

    @pytest.mark.asyncio
    async def test_update_rtmp_endpoint_not_found(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test updating non-existent rtmp endpoint"""
        app.dependency_overrides[get_current_user] = mock_current_user

        non_existent_id = uuid4()
        update_data = {"title": "Updated Title"}

        response = await client.put(
            f"/api/stream-endpoint/{non_existent_id}", json=update_data
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "Stream settings not found"

    @pytest.mark.asyncio
    async def test_delete_rtmp_endpoint_success(
        self,
        client: AsyncClient,
        test_user: User,
        test_stream_settings: RtmpEndpoint,
        mock_current_user,
    ):
        """Test successful rtmp endpoint deletion"""
        app.dependency_overrides[get_current_user] = mock_current_user

        response = await client.delete(
            f"/api/stream-endpoint/{test_stream_settings.id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Stream settings deleted successfully"
        assert data["id"] == str(test_stream_settings.id)

    @pytest.mark.asyncio
    async def test_delete_rtmp_endpoint_not_found(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test deleting non-existent rtmp endpoint"""
        app.dependency_overrides[get_current_user] = mock_current_user

        non_existent_id = uuid4()
        response = await client.delete(f"/api/stream-endpoint/{non_existent_id}")

        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "Stream settings not found"

    @pytest.mark.asyncio
    async def test_delete_rtmp_endpoint_unauthorized_user(
        self,
        client: AsyncClient,
        test_stream_settings: RtmpEndpoint,
        mock_current_user,
        db_session,
    ):
        """Test deleting rtmp endpoint as unauthorized user"""
        # Create a different user
        different_user = User(
            id=uuid4(),
            keycloak_id=f"different-keycloak-id-{uuid4()}",
            username=f"differentuser-{uuid4()}",
            email=f"different-{uuid4()}@example.com",
            first_name="Different",
            last_name="User",
        )
        db_session.add(different_user)
        await db_session.commit()

        # Mock current user as the different user
        def mock_different_user():
            return different_user

        app.dependency_overrides[get_current_user] = mock_different_user

        response = await client.delete(
            f"/api/stream-endpoint/{test_stream_settings.id}"
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "Stream settings not found"

    @pytest.mark.asyncio
    async def test_create_multiple_rtmp_endpoints(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test creating multiple rtmp endpoints for the same user"""
        app.dependency_overrides[get_current_user] = mock_current_user

        # Create first rtmp endpoint
        stream_data_1 = {
            "title": "Stream 1",
            "stream_key": "key-1",
            "rtmp_url": "rtmp://test1.example.com/live",
        }
        response1 = await client.post("/api/stream-endpoint/create", json=stream_data_1)
        assert response1.status_code == 200

        # Create second rtmp endpoint
        stream_data_2 = {
            "title": "Stream 2",
            "stream_key": "key-2",
            "rtmp_url": "rtmp://test2.example.com/live",
        }
        response2 = await client.post("/api/stream-endpoint/create", json=stream_data_2)
        assert response2.status_code == 200

        # Get all rtmp endpoints for user
        response = await client.get("/api/stream-endpoint/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_invalid_uuid_format(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test endpoints with invalid UUID format"""
        app.dependency_overrides[get_current_user] = mock_current_user

        invalid_uuid = "invalid-uuid"
        response = await client.get(f"/api/stream-endpoint/{invalid_uuid}")

        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_update_with_empty_data(
        self,
        client: AsyncClient,
        test_user: User,
        test_stream_settings: RtmpEndpoint,
        mock_current_user,
    ):
        """Test updating rtmp endpoint with empty data"""
        app.dependency_overrides[get_current_user] = mock_current_user

        update_data: dict[str, str] = {}

        response = await client.put(
            f"/api/stream-endpoint/{test_stream_settings.id}", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        # Should return unchanged data
        assert data["title"] == test_stream_settings.title
        assert data["stream_key"] == test_stream_settings.stream_key
        assert data["rtmp_url"] == test_stream_settings.rtmp_url
