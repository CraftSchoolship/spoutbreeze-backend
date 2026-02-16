import pytest
from app.services.broadcaster_service import _clamp_resolution, StreamTracker


class TestClampResolution:
    """Test resolution clamping logic"""

    def test_requested_within_limit(self):
        assert _clamp_resolution("720p", "1080p") == "720p"

    def test_requested_at_limit(self):
        assert _clamp_resolution("1080p", "1080p") == "1080p"

    def test_requested_exceeds_limit(self):
        assert _clamp_resolution("4K", "1080p") == "1080p"

    def test_none_requested_returns_max(self):
        assert _clamp_resolution(None, "1080p") == "1080p"

    def test_invalid_requested_returns_max(self):
        assert _clamp_resolution("invalid", "720p") == "720p"

    def test_invalid_max_quality_defaults_720p(self):
        assert _clamp_resolution("1080p", "invalid") == "720p"

    def test_lowest_quality(self):
        assert _clamp_resolution("360p", "4K") == "360p"


class TestStreamTrackerFallback:
    """Test StreamTracker with in-memory fallback (Redis not connected)"""

    @pytest.mark.anyio
    async def test_add_and_count_streams(self):
        # Clear any existing fallback data
        StreamTracker._fallback_user_streams.clear()
        StreamTracker._fallback_stream_to_user.clear()
        StreamTracker._fallback_stream_platforms.clear()

        user_id = "test_user_1"
        await StreamTracker.add_stream(user_id, "stream_1", "twitch")
        await StreamTracker.add_stream(user_id, "stream_2", "youtube")

        count = await StreamTracker.get_active_stream_count(user_id)
        assert count == 2

    @pytest.mark.anyio
    async def test_remove_stream(self):
        StreamTracker._fallback_user_streams.clear()
        StreamTracker._fallback_stream_to_user.clear()
        StreamTracker._fallback_stream_platforms.clear()

        user_id = "test_user_2"
        await StreamTracker.add_stream(user_id, "stream_3", "twitch")

        uid, platform = await StreamTracker.remove_stream("stream_3")
        assert uid == user_id
        assert platform == "twitch"

        count = await StreamTracker.get_active_stream_count(user_id)
        assert count == 0

    @pytest.mark.anyio
    async def test_get_user_streams(self):
        StreamTracker._fallback_user_streams.clear()
        StreamTracker._fallback_stream_to_user.clear()
        StreamTracker._fallback_stream_platforms.clear()

        user_id = "test_user_3"
        await StreamTracker.add_stream(user_id, "s1")
        await StreamTracker.add_stream(user_id, "s2")

        streams = await StreamTracker.get_user_streams(user_id)
        assert streams == {"s1", "s2"}

    @pytest.mark.anyio
    async def test_remove_nonexistent_stream(self):
        StreamTracker._fallback_user_streams.clear()
        StreamTracker._fallback_stream_to_user.clear()
        StreamTracker._fallback_stream_platforms.clear()

        uid, platform = await StreamTracker.remove_stream("nonexistent")
        assert uid is None
        assert platform is None
