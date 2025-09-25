from app.models.stream_models import RtmpEndpoint
from app.models.user_models import User
from app.models.stream_schemas import (
    RtmpEndpointResponse,
    RtmpEndpointUpdate,
    CreateRtmpEndpointCreate,
    RtmpEndpointDeleteResponse,
)
from uuid import UUID
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from app.config.logger_config import logger


class RtmpEndpointService:
    """
    Service for creating the stream settings
    """

    def _create_rtmp_endpoints_response(
        self, rtmp_endpoints: RtmpEndpoint, user: User
    ) -> RtmpEndpointResponse:
        """
        Create a RtmpEndpointResponse with user information.
        """
        return RtmpEndpointResponse(
            id=rtmp_endpoints.id,
            title=rtmp_endpoints.title,
            stream_key=rtmp_endpoints.stream_key,
            rtmp_url=rtmp_endpoints.rtmp_url,
            user_id=rtmp_endpoints.user_id,
            user_first_name=user.first_name,
            user_last_name=user.last_name,
            created_at=rtmp_endpoints.created_at,
            updated_at=rtmp_endpoints.updated_at,
        )

    async def create_rtmp_endpoints(
        self,
        rtmp_endpoints: CreateRtmpEndpointCreate,
        user_id: UUID,
        db: AsyncSession,
    ) -> RtmpEndpointResponse:
        """
        Create a stream settings for a user
        """
        try:
            new_rtmp_endpoints = RtmpEndpoint(
                title=rtmp_endpoints.title,
                stream_key=rtmp_endpoints.stream_key,
                rtmp_url=rtmp_endpoints.rtmp_url,
                user_id=user_id,
            )
            db.add(new_rtmp_endpoints)
            await db.commit()
            await db.refresh(new_rtmp_endpoints)

            # Get the user information
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one()

            logger.info(
                f"Stream settings with the name {new_rtmp_endpoints.title} created for user {user_id}"
            )
            return self._create_rtmp_endpoints_response(new_rtmp_endpoints, user)
        except IntegrityError as e:
            await db.rollback()
            error_msg = str(e.orig)

            if "stream_endpoints_stream_key_key" in error_msg:
                logger.warning(
                    f"Duplicate stream key attempted: {rtmp_endpoints.stream_key}"
                )
                raise ValueError(
                    "Stream key already exists. Please use a different stream key."
                )
            elif "stream_endpoints_title_key" in error_msg or "title" in error_msg:
                logger.warning(f"Duplicate title attempted: {rtmp_endpoints.title}")
                raise ValueError("Title already exists. Please use a different title.")
            else:
                logger.error(f"Integrity constraint violation: {error_msg}")
                raise ValueError(
                    "A unique constraint was violated. Please check your input data."
                )
        except Exception as e:
            logger.error(f"Error creating stream settings: {e}")
            await db.rollback()
            raise

    async def get_all_rtmp_endpoints(
        self,
        db: AsyncSession,
    ) -> List[RtmpEndpointResponse]:
        """
        Get all stream settings
        """
        try:
            result = await db.execute(
                select(RtmpEndpoint, User).join(User, RtmpEndpoint.user_id == User.id)
            )
            rtmp_endpoints_user_pairs = result.all()

            rtmp_endpoints_list = [
                self._create_rtmp_endpoints_response(rtmp_endpoints, user)
                for rtmp_endpoints, user in rtmp_endpoints_user_pairs
            ]

            logger.info(f"Retrieved {len(rtmp_endpoints_list)} stream settings")
            return rtmp_endpoints_list
        except Exception as e:
            logger.error(f"Error retrieving stream settings: {str(e)}")
            raise e

    async def get_rtmp_endpoints_by_user_id(
        self,
        user_id: UUID,
        db: AsyncSession,
    ) -> List[RtmpEndpointResponse]:
        """
        Get all stream settings for a user
        """
        try:
            result = await db.execute(
                select(RtmpEndpoint, User)
                .join(User, RtmpEndpoint.user_id == User.id)
                .where(RtmpEndpoint.user_id == user_id)
            )
            rtmp_endpoints_user_pairs = result.all()

            rtmp_endpoints_list = [
                self._create_rtmp_endpoints_response(rtmp_endpoints, user)
                for rtmp_endpoints, user in rtmp_endpoints_user_pairs
            ]

            logger.info(
                f"Retrieved {len(rtmp_endpoints_list)} stream settings for user {user_id}"
            )
            return rtmp_endpoints_list
        except Exception as e:
            logger.error(f"Error retrieving stream settings for user {user_id}: {e}")
            raise

    async def get_rtmp_endpoints_by_id(
        self,
        rtmp_endpoints_id: UUID,
        db: AsyncSession,
    ) -> Optional[RtmpEndpointResponse]:
        """
        Get stream settings by ID
        """
        try:
            result = await db.execute(
                select(RtmpEndpoint, User)
                .join(User, RtmpEndpoint.user_id == User.id)
                .where(RtmpEndpoint.id == rtmp_endpoints_id)
            )
            rtmp_endpoints_user_pair = result.first()

            if rtmp_endpoints_user_pair:
                rtmp_endpoints, user = rtmp_endpoints_user_pair
                logger.info(f"Stream settings retrieved with ID {rtmp_endpoints_id}")
                return self._create_rtmp_endpoints_response(rtmp_endpoints, user)
            else:
                logger.warning(f"Stream settings with ID {rtmp_endpoints_id} not found")
                return None
        except Exception as e:
            logger.error(
                f"Error retrieving stream settings with ID {rtmp_endpoints_id}: {e}"
            )
            raise

    async def update_rtmp_endpoints(
        self,
        rtmp_endpoints_id: UUID,
        rtmp_endpoints_update: RtmpEndpointUpdate,
        db: AsyncSession,
    ) -> Optional[RtmpEndpointResponse]:
        """
        Update stream settings by ID
        """
        try:
            # Check if the stream settings exist and get user info
            result = await db.execute(
                select(RtmpEndpoint, User)
                .join(User, RtmpEndpoint.user_id == User.id)
                .where(RtmpEndpoint.id == rtmp_endpoints_id)
            )
            rtmp_endpoints_user_pair = result.first()

            if not rtmp_endpoints_user_pair:
                logger.warning(f"Stream settings with ID {rtmp_endpoints_id} not found")
                return None

            rtmp_endpoints, user = rtmp_endpoints_user_pair

            # Update the stream settings
            update_data = {
                k: v
                for k, v in rtmp_endpoints_update.model_dump().items()
                if v is not None
            }

            if update_data:
                update_stmt = (
                    update(RtmpEndpoint)
                    .where(RtmpEndpoint.id == rtmp_endpoints_id)
                    .values(**update_data)
                )
                await db.execute(update_stmt)
                await db.commit()
                await db.refresh(rtmp_endpoints)

            logger.info(
                f"Stream settings with ID {rtmp_endpoints_id} updated for user {rtmp_endpoints.user_id}"
            )
            return self._create_rtmp_endpoints_response(rtmp_endpoints, user)
        except Exception as e:
            logger.error(
                f"Error updating stream settings with ID {rtmp_endpoints_id}: {e}"
            )
            await db.rollback()
            raise

    async def delete_rtmp_endpoints(
        self,
        rtmp_endpoints_id: UUID,
        user_id: UUID,
        db: AsyncSession,
    ) -> Optional[RtmpEndpointDeleteResponse]:
        """
        Delete stream settings by ID
        """
        try:
            # Check if the stream settings exist
            select_stmt = select(RtmpEndpoint).where(
                RtmpEndpoint.id == rtmp_endpoints_id,
                RtmpEndpoint.user_id == user_id,
            )
            result = await db.execute(select_stmt)
            rtmp_endpoints = result.scalars().first()
            if not rtmp_endpoints:
                logger.warning(f"Stream settings with ID {rtmp_endpoints_id} not found")
                return None

            # Delete the stream settings
            delete_stmt = delete(RtmpEndpoint).where(
                RtmpEndpoint.id == rtmp_endpoints_id
            )
            await db.execute(delete_stmt)
            await db.commit()
            logger.info(
                f"Stream settings with ID {rtmp_endpoints_id} deleted for user {user_id}"
            )
            return RtmpEndpointDeleteResponse(
                message="Stream settings deleted successfully",
                id=rtmp_endpoints_id,
            )
        except Exception as e:
            logger.error(
                f"Error deleting stream settings with ID {rtmp_endpoints_id}: {e}"
            )
            await db.rollback()
            raise
