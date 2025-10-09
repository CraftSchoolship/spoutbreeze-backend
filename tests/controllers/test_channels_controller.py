import pytest
from httpx import AsyncClient
from uuid import uuid4

from app.main import app
from app.controllers.user_controller import get_current_user
from app.models.user_models import User
from app.models.channel.channels_model import Channel


class TestChannelsController:
    """Test cases for channels controller"""

    @pytest.mark.asyncio
    async def test_create_channel_success(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test successful channel creation"""
        # Override the get_current_user dependency
        app.dependency_overrides[get_current_user] = mock_current_user

        channel_data = {"name": "New Test Channel"}

        response = await client.post("/api/channels/", json=channel_data)

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Test Channel"
        assert data["creator_id"] == str(test_user.id)
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    @pytest.mark.asyncio
    async def test_create_channel_duplicate_name(
        self,
        client: AsyncClient,
        test_user: User,
        test_channel: Channel,
        mock_current_user,
    ):
        """Test creating channel with duplicate name"""
        app.dependency_overrides[get_current_user] = mock_current_user

        channel_data = {
            "name": test_channel.name  # Use existing channel name
        }

        response = await client.post("/api/channels/", json=channel_data)

        # Should fail due to unique constraint
        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_get_channels_by_user(
        self,
        client: AsyncClient,
        test_user: User,
        test_channel: Channel,
        mock_current_user,
    ):
        """Test getting channels for current user"""
        app.dependency_overrides[get_current_user] = mock_current_user

        response = await client.get("/api/channels/")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert len(data["channels"]) == 1
        assert data["channels"][0]["id"] == str(test_channel.id)
        assert data["channels"][0]["name"] == test_channel.name

    @pytest.mark.asyncio
    async def test_get_channels_by_user_no_channels(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test getting channels when user has no channels"""
        app.dependency_overrides[get_current_user] = mock_current_user

        response = await client.get("/api/channels/")

        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "No channels found"

    @pytest.mark.asyncio
    async def test_get_all_channels(
        self,
        client: AsyncClient,
        test_user: User,
        test_channel: Channel,
        mock_current_user,
    ):
        """Test getting all channels"""
        app.dependency_overrides[get_current_user] = mock_current_user

        response = await client.get("/api/channels/all")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert len(data["channels"]) >= 1

    @pytest.mark.asyncio
    async def test_get_channel_by_id_success(
        self,
        client: AsyncClient,
        test_user: User,
        test_channel: Channel,
        mock_current_user,
    ):
        """Test getting channel by ID"""
        app.dependency_overrides[get_current_user] = mock_current_user

        response = await client.get(f"/api/channels/{test_channel.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(test_channel.id)
        assert data["name"] == test_channel.name
        assert data["creator_id"] == str(test_user.id)

    @pytest.mark.asyncio
    async def test_get_channel_by_id_not_found(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test getting non-existent channel"""
        app.dependency_overrides[get_current_user] = mock_current_user

        non_existent_id = uuid4()
        response = await client.get(f"/api/channels/{non_existent_id}")

        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "Channel not found"

    @pytest.mark.asyncio
    async def test_update_channel_success(
        self,
        client: AsyncClient,
        test_user: User,
        test_channel: Channel,
        mock_current_user,
    ):
        """Test successful channel update"""
        app.dependency_overrides[get_current_user] = mock_current_user

        update_data = {"name": "Updated Channel Name"}

        response = await client.put(
            f"/api/channels/{test_channel.id}", json=update_data
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Channel Name"
        assert data["id"] == str(test_channel.id)

    @pytest.mark.asyncio
    async def test_update_channel_not_found(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test updating non-existent channel"""
        app.dependency_overrides[get_current_user] = mock_current_user

        non_existent_id = uuid4()
        update_data = {"name": "Updated Name"}

        response = await client.put(
            f"/api/channels/{non_existent_id}", json=update_data
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "Channel not found"

    @pytest.mark.asyncio
    async def test_delete_channel_success(
        self,
        client: AsyncClient,
        test_user: User,
        test_channel: Channel,
        mock_current_user,
    ):
        """Test successful channel deletion"""
        app.dependency_overrides[get_current_user] = mock_current_user

        response = await client.delete(f"/api/channels/{test_channel.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["message"] == "Channel deleted successfully"

    @pytest.mark.asyncio
    async def test_delete_channel_not_found(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test deleting non-existent channel"""
        app.dependency_overrides[get_current_user] = mock_current_user

        non_existent_id = uuid4()
        response = await client.delete(f"/api/channels/{non_existent_id}")

        # This should not raise an error in your current implementation
        # but in a production app, you might want to return 404
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_channel_recordings_success(
        self,
        client: AsyncClient,
        test_user: User,
        test_channel: Channel,
        mock_current_user,
        mocker,
    ):
        """Test getting channel recordings"""
        app.dependency_overrides[get_current_user] = mock_current_user

        # Mock the channels_service.get_channel_recordings method
        mock_recordings = {"recordings": [], "total_recordings": 0}

        mock_service = mocker.patch(
            "app.controllers.channels_controller.channels_service.get_channel_recordings"
        )
        mock_service.return_value = mock_recordings

        response = await client.get(f"/api/channels/{test_channel.id}/recordings")

        assert response.status_code == 200
        data = response.json()
        assert data["recordings"] == []
        assert data["total_recordings"] == 0

    @pytest.mark.asyncio
    async def test_invalid_uuid_format(
        self, client: AsyncClient, test_user: User, mock_current_user
    ):
        """Test endpoints with invalid UUID format"""
        app.dependency_overrides[get_current_user] = mock_current_user

        invalid_uuid = "invalid-uuid"
        response = await client.get(f"/api/channels/{invalid_uuid}")

        assert response.status_code == 422  # Validation error
