from typing import Optional

from pydantic import BaseModel, Field, HttpUrl


class ScrapeRequest(BaseModel):
    url: HttpUrl = Field(..., description="A Pinterest pin or pin.it URL")


class MediaItem(BaseModel):
    index: int
    media_type: str
    thumbnail_url: str
    video_url: Optional[str] = None


class ScrapeResponse(BaseModel):
    shortcode: str
    caption: str = ""
    author: str = ""
    media_type: str
    thumbnail_url: str
    video_url: Optional[str] = None
    audio_url: Optional[str] = None
    cover_url: Optional[str] = None
    duration: Optional[int] = None
    title: Optional[str] = None
    album: Optional[str] = None
    carousel: Optional[list[MediaItem]] = None
    tracklist: Optional[list[dict]] = None


class ExtractedMedia(BaseModel):
    pin_id: str
    canonical_url: str
    caption: str = ""
    author: str = ""
    image_urls: list[str] = Field(default_factory=list)
    video_url: Optional[str] = None
    thumbnail_url: Optional[str] = None

