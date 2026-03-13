from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logger_config import logger
from app.models.channel.channels_model import Channel
from app.models.channel.channels_schemas import (
    ChannelCreate,
    ChannelResponse,
    ChannelUpdate,
)
from app.models.user_models import User


class ChannelsService:
    """
    Service class for managing channels.
    """

    def _create_channel_response(self, channel: Channel, creator: User) -> ChannelResponse:
        """
        Create a ChannelResponse with creator information.
        """
        return ChannelResponse(
            id=channel.id,
            name=channel.name,
            creator_id=channel.creator_id,
            creator_first_name=creator.first_name,
            creator_last_name=creator.last_name,
            created_at=channel.created_at,
            updated_at=channel.updated_at,
        )

    async def create_channel(
        self,
        db: AsyncSession,
        channel_create: ChannelCreate,
        user_id: UUID,
    ) -> ChannelResponse:
        """
        Create a new channel.
        """
        try:
            new_channel = Channel(
                name=channel_create.name,
                creator_id=user_id,
            )
            db.add(new_channel)
            await db.commit()
            await db.refresh(new_channel)

            # Get the creator information
            creator_result = await db.execute(select(User).where(User.id == user_id))
            creator = creator_result.scalar_one()

            logger.info(f"Channel {new_channel.name} created for user {user_id}")
            return self._create_channel_response(new_channel, creator)
        except Exception as e:
            logger.error(f"Error creating channel: {e}")
            await db.rollback()
            raise

    async def get_channels_by_user_id(
        self,
        db: AsyncSession,
        user_id: UUID,
    ) -> list[ChannelResponse]:
        """
        Get all channels for a user.
        """
        try:
            result = await db.execute(
                select(Channel, User).join(User, Channel.creator_id == User.id).where(Channel.creator_id == user_id)
            )
            channel_creator_pairs = result.all()

            channels = [self._create_channel_response(channel, creator) for channel, creator in channel_creator_pairs]

            logger.info(f"Retrieved {len(channels)} channels for user {user_id}")
            return channels
        except Exception as e:
            logger.error(f"Error retrieving channels for user {user_id}: {e}")
            raise

    async def get_channel_by_id(
        self,
        db: AsyncSession,
        channel_id: UUID,
    ) -> ChannelResponse | None:
        """
        Get a channel by its ID.
        """
        try:
            result = await db.execute(
                select(Channel, User).join(User, Channel.creator_id == User.id).where(Channel.id == channel_id)
            )
            channel_creator_pair = result.first()

            if channel_creator_pair:
                channel, creator = channel_creator_pair
                logger.info(f"Retrieved channel {channel.name} with ID {channel_id}")
                return self._create_channel_response(channel, creator)
            else:
                logger.warning(f"Channel with ID {channel_id} not found")
                return None
        except Exception as e:
            logger.error(f"Error retrieving channel with ID {channel_id}: {e}")
            raise

    async def get_channels(
        self,
        db: AsyncSession,
    ) -> list[ChannelResponse]:
        """
        Get all channels.
        """
        try:
            result = await db.execute(select(Channel, User).join(User, Channel.creator_id == User.id))
            channel_creator_pairs = result.all()

            channels = [self._create_channel_response(channel, creator) for channel, creator in channel_creator_pairs]

            logger.info(f"Retrieved {len(channels)} channels")
            return channels
        except Exception as e:
            logger.error(f"Error retrieving channels: {e}")
            raise

    async def get_channel_by_name(
        self,
        db: AsyncSession,
        channel_name: str,
        user_id: UUID,
    ) -> Channel | None:
        """
        Get a channel by its name.
        """
        try:
            result = await db.execute(
                select(Channel).where(
                    Channel.name == channel_name,
                    Channel.creator_id == user_id,
                )
            )
            channel = result.scalar_one_or_none()
            if channel:
                logger.info(f"Retrieved channel {channel.name} for user {user_id}")
            else:
                logger.warning(f"Channel with name {channel_name} not found for user {user_id}")
            return channel
        except Exception as e:
            logger.error(f"Error retrieving channel with name {channel_name}: {e}")
            raise

    async def update_channel(
        self,
        db: AsyncSession,
        channel_id: UUID,
        channel_update: ChannelUpdate,
        user_id: UUID,
    ) -> ChannelResponse | None:
        """
        Update a channel by ID.
        """
        #  Check if the channel exists and belongs to the user
        query = (
            select(Channel, User)
            .join(User, Channel.creator_id == User.id)
            .where(
                Channel.id == channel_id,
                Channel.creator_id == user_id,
            )
        )
        result = await db.execute(query)
        channel_creator_pair = result.first()

        if not channel_creator_pair:
            logger.warning(f"Channel with ID {channel_id} not found or does not belong to user {user_id}")
            return None

        channel, creator = channel_creator_pair

        try:
            update_data = {k: v for k, v in channel_update.model_dump().items() if v is not None}
            update_stmt = update(Channel).where(Channel.id == channel_id).values(**update_data)
            await db.execute(update_stmt)
            await db.commit()
            await db.refresh(channel)
            logger.info(f"Channel {channel.name} updated for user {user_id}")
            return self._create_channel_response(channel, creator)
        except Exception as e:
            logger.error(f"Error updating channel with ID {channel_id}: {e}")
            await db.rollback()
            raise

    async def delete_channel(
        self,
        db: AsyncSession,
        channel_id: UUID,
        user_id: UUID,
    ) -> bool:
        """
        Delete a channel by ID.
        """
        # Check if the channel exists and belongs to the user
        query = select(Channel).where(
            Channel.id == channel_id,
            Channel.creator_id == user_id,
        )
        result = await db.execute(query)
        channel = result.scalar_one_or_none()
        if not channel:
            logger.warning(f"Channel with ID {channel_id} not found or does not belong to user {user_id}")
            return False
        try:
            delete_stmt = delete(Channel).where(Channel.id == channel_id)
            await db.execute(delete_stmt)
            await db.commit()
            logger.info(f"Channel {channel.name} deleted for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting channel with ID {channel_id}: {e}")
            await db.rollback()
            raise

    # async def get_or_create_channel(
    #     self,
    #     db: AsyncSession,
    #     name: str,
    #     user_id: UUID,
    # ) -> ChannelResponse:
    #     """
    #     Get or create a channel.
    #     """
    #     try:
    #         # Check if channel exists with creator info
    #         result = await db.execute(
    #             select(Channel, User)
    #             .join(User, Channel.creator_id == User.id)
    #             .where(
    #                 Channel.name == name,
    #                 Channel.creator_id == user_id,
    #             )
    #         )
    #         channel_creator_pair = result.first()

    #         if channel_creator_pair:
    #             channel, creator = channel_creator_pair
    #             logger.info(f"Channel {channel.name} already exists for user {user_id}")
    #             return self._create_channel_response(channel, creator)
    #         else:
    #             channel_create = ChannelCreate(name=name)
    #             new_channel = await self.create_channel(db, channel_create, user_id)
    #             logger.info(f"Channel {name} created for user {user_id}")
    #             return new_channel
    #     except Exception as e:
    #         logger.error(
    #             f"Error getting or creating channel {name} for user {user_id}: {e}"
    #         )
    #         raise

    async def get_channel_recordings(
        self,
        db: AsyncSession,
        channel_id: UUID,
        user_id: UUID,
    ) -> dict[str, Any]:
        """
        Get all recordings for events in a specific channel.
        """
        try:
            # Check if channel exists and belongs to user
            channel = await self.get_channel_by_id(db=db, channel_id=channel_id)
            if not channel:
                raise ValueError(f"Channel with ID {channel_id} does not exist.")

            if channel.creator_id != user_id:
                raise ValueError(f"Channel does not belong to user {user_id}.")

            # Get all events for this channel that have meetings
            from app.models.event.event_models import Event

            events_result = await db.execute(
                select(Event).where(
                    Event.channel_id == channel_id,
                    Event.meeting_id.isnot(None),
                    # Event.status == EventStatus.ENDED, # Uncomment in production
                )
            )
            events = events_result.scalars().all()

            if not events:
                logger.info(f"No events with meetings found for channel ID {channel_id}")
                return {
                    "recordings": [],
                    "total_recordings": 0,
                }

            # Get recordings for all events in parallel
            import asyncio

            from app.models.bbb_schemas import GetRecordingRequest
            from app.services.bbb_service import BBBService

            bbb_service = BBBService()

            async def get_event_recordings(event):
                """Get recordings for a single event"""
                try:
                    recording_request = GetRecordingRequest(meeting_id=event.meeting_id)
                    # Make this async if possible, or use asyncio.to_thread for sync calls
                    loop = asyncio.get_event_loop()
                    recordings_response = await loop.run_in_executor(None, bbb_service.get_recordings, recording_request)

                    if recordings_response.get("returncode") == "SUCCESS":
                        recordings = recordings_response.get("recordings", [])
                        return recordings if recordings else []
                    return []
                except Exception as e:
                    logger.warning(f"Failed to get recordings for event {event.id}: {e}")
                    return []

            # Execute all API calls in parallel
            tasks = [get_event_recordings(event) for event in events]
            recordings_results = await asyncio.gather(*tasks, return_exceptions=True)

            # Flatten results
            all_recordings: list[dict[str, Any]] = []
            for result in recordings_results:
                if isinstance(result, list):
                    all_recordings.extend(result)
                elif isinstance(result, Exception):
                    logger.warning(f"Task failed with exception: {result}")

            return {
                "recordings": all_recordings,
                "total_recordings": len(all_recordings),
            }

        except Exception as e:
            logger.error(f"Error getting recordings for channel {channel_id}: {e}")
            raise
