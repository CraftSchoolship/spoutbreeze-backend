from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ChannelBase(BaseModel):
    """
    Base model for channel
    """

    name: str


class ChannelCreate(ChannelBase):
    """
    Create model for channel
    """

    pass


class ChannelUpdate(BaseModel):
    """
    Update model for channel
    """

    name: str | None = None


class ChannelResponse(ChannelBase):
    """
    Response model for channel
    """

    id: UUID
    creator_id: UUID
    creator_first_name: str
    creator_last_name: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChannelListResponse(BaseModel):
    """
    List response model for channel
    """

    channels: list[ChannelResponse]
    total: int

    model_config = ConfigDict(from_attributes=True)
