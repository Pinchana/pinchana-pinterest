from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

from .extractor import (
    InvalidPinterestUrl,
    PinterestBoardNotSupported,
    PinterestError,
    PinterestExtractor,
    PinterestNotFound,
)
from .models import ExtractedMedia, MediaItem, ScrapeRequest, ScrapeResponse
from .storage import MediaStorage, MediaTooLarge

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

storage = MediaStorage(
    base_path=os.getenv("CACHE_PATH", "./cache"),
    max_size_gb=float(os.getenv("CACHE_MAX_SIZE_GB", "2")),
    max_item_mb=float(os.getenv("MAX_MEDIA_SIZE_MB", "100")),
)
extractor = PinterestExtractor(timeout=int(os.getenv("PINTEREST_TIMEOUT_SECONDS", "30")))
app = FastAPI(title="Pinchana Pinterest", version="0.1.0")
_pin_locks: dict[str, asyncio.Lock] = {}


def _local_url(pin_id: str, filename: str) -> str:
    return f"/media/pinterest/{pin_id}/{filename}"


def _cached_response(metadata: dict) -> ScrapeResponse:
    return ScrapeResponse(**metadata["response"])


async def _download_pin(extracted: ExtractedMedia) -> ScrapeResponse:
    pin_id = extracted.pin_id
    lock = _pin_locks.setdefault(pin_id, asyncio.Lock())
    async with lock:
        cached = storage.load(pin_id)
        if cached:
            return _cached_response(cached)

        local_files: list[str] = []
        image_names: list[str] = []
        video_name: str | None = None

        try:
            if extracted.video_url:
                video_name = "video.mp4"
                await storage.download(extracted.video_url, pin_id, video_name)
                local_files.append(video_name)

            max_images = max(1, min(20, storage.max_size // storage.max_item_size))
            image_urls = extracted.image_urls[:max_images]
            if extracted.thumbnail_url and extracted.thumbnail_url not in image_urls:
                image_urls.insert(0, extracted.thumbnail_url)
                image_urls = image_urls[:max_images]
            for index, image_url in enumerate(image_urls):
                name = f"image_{index}.jpg"
                await storage.download(image_url, pin_id, name)
                image_names.append(name)
                local_files.append(name)
        except Exception:
            # An incomplete entry has no metadata and is replaced on the next request.
            logger.exception("Failed downloading pin %s", pin_id)
            raise

        if not local_files:
            raise PinterestNotFound("No downloadable Pinterest media found")

        thumbnail = _local_url(pin_id, image_names[0]) if image_names else ""
        carousel = None
        if not video_name and len(image_names) > 1:
            carousel = [
                MediaItem(index=index, media_type="image", thumbnail_url=_local_url(pin_id, name))
                for index, name in enumerate(image_names)
            ]

        response = ScrapeResponse(
            shortcode=pin_id,
            caption=extracted.caption,
            author=extracted.author,
            media_type="video" if video_name else "image",
            thumbnail_url=thumbnail,
            video_url=_local_url(pin_id, video_name) if video_name else None,
            carousel=carousel,
            title=extracted.caption or None,
        )
        storage.save(pin_id, {"response": response.model_dump(), "local_files": local_files})
        return response


async def scrape(url: str) -> ScrapeResponse:
    extracted = await extractor.extract(url)
    return await _download_pin(extracted)


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, InvalidPinterestUrl):
        raise HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, PinterestBoardNotSupported):
        raise HTTPException(status_code=501, detail=str(exc))
    if isinstance(exc, PinterestNotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MediaTooLarge):
        raise HTTPException(status_code=413, detail=str(exc))
    if isinstance(exc, PinterestError):
        raise HTTPException(status_code=502, detail=str(exc))
    logger.exception("Unexpected Pinterest failure", exc_info=exc)
    raise HTTPException(status_code=500, detail="Pinterest processing failed")


@app.post("/scrape", response_model=ScrapeResponse)
async def process_scrape_request(payload: ScrapeRequest):
    try:
        return await scrape(str(payload.url))
    except Exception as exc:
        _raise_http(exc)


@app.get("/v2/pinterest")
async def legacy_pinterest(request: Request, link: str, x_api_key: str | None = Header(default=None)):
    configured_key = os.getenv("PINTEREST_API_KEY")
    if configured_key and x_api_key != configured_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    try:
        response = await scrape(link)
    except Exception as exc:
        _raise_http(exc)

    def absolute(path: str | None) -> str | None:
        if not path:
            return None
        public = os.getenv("PUBLIC_BASE_URL", str(request.base_url)).rstrip("/")
        return f"{public}{path}"

    if response.video_url:
        return {"type": "video", "videos": [absolute(response.video_url)]}
    images = response.carousel or [
        MediaItem(index=0, media_type="image", thumbnail_url=response.thumbnail_url)
    ]
    return {"type": "image", "images": [absolute(item.thumbnail_url) for item in images]}


@app.get("/media/{platform}/{pin_id}/{filename}")
async def serve_media(platform: str, pin_id: str, filename: str):
    if platform != "pinterest" or not pin_id.isdigit() or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Invalid media path")
    base = storage.entry(pin_id).resolve()
    path = (base / filename).resolve()
    if path.parent != base or not path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(path)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "pinterest",
        "cache_max_bytes": storage.max_size,
        "media_max_bytes": storage.max_item_size,
    }
