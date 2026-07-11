"""Pinterest pin metadata extraction with browser and yt-dlp fallbacks."""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import logging
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession
from yt_dlp import YoutubeDL

from .models import ExtractedMedia

logger = logging.getLogger(__name__)

_PIN_ID_RE = re.compile(r"/pin/(?:[^/?#]*--)?(?P<id>\d+)(?:/|$)", re.IGNORECASE)
_ALLOWED_HOSTS = {"pin.it", "pinterest.com"}
_VIDEO_KEYS = {"contenturl", "video_url", "videourl", "url"}


class PinterestError(RuntimeError):
    pass


class InvalidPinterestUrl(PinterestError):
    pass


class PinterestBoardNotSupported(PinterestError):
    pass


class PinterestNotFound(PinterestError):
    pass


def _is_allowed_host(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in _ALLOWED_HOSTS)


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not _is_allowed_host(parsed.hostname):
        raise InvalidPinterestUrl("Only pinterest.com and pin.it URLs are accepted")


def extract_pin_id(url: str) -> str | None:
    match = _PIN_ID_RE.search(urlparse(url).path)
    return match.group("id") if match else None


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.json_scripts: list[str] = []
        self._json_depth = 0
        self._json_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value is not None}
        if tag.lower() == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content")
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag.lower() == "script" and "json" in values.get("type", "").lower():
            self._json_depth += 1
            self._json_parts = []

    def handle_data(self, data: str) -> None:
        if self._json_depth:
            self._json_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._json_depth:
            value = "".join(self._json_parts).strip()
            if value:
                self.json_scripts.append(value)
            self._json_depth = 0
            self._json_parts = []


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _url(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        return html_lib.unescape(value).replace("\\u002F", "/")
    return None


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _best_image(images: Any) -> str | None:
    candidates: list[tuple[int, str]] = []
    for node in _walk(images):
        if not isinstance(node, dict):
            continue
        candidate = _url(node.get("url") or node.get("src") or node.get("contentUrl"))
        if candidate:
            score = int(node.get("width") or 0) * int(node.get("height") or 0)
            candidates.append((score, candidate))
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def _best_video(value: Any) -> str | None:
    candidates: list[tuple[int, str]] = []
    for node in _walk(value):
        if not isinstance(node, dict):
            continue
        kind = str(node.get("@type") or node.get("type") or "").lower()
        for key, raw in node.items():
            candidate = _url(raw)
            key_lower = str(key).lower()
            if not candidate or key_lower not in _VIDEO_KEYS:
                continue
            if "video" not in kind and not any(part in candidate.lower() for part in (".mp4", ".m3u8")):
                continue
            score = int(node.get("width") or 0) * int(node.get("height") or 0)
            if ".mp4" in candidate.lower():
                score += 10**12
            candidates.append((score, candidate))
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def parse_pin_html(document: str, pin_id: str, canonical_url: str) -> ExtractedMedia:
    parser = _MetadataParser()
    parser.feed(document)

    objects: list[Any] = []
    for raw in parser.json_scripts:
        try:
            objects.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue

    matching_nodes = [
        node for obj in objects for node in _walk(obj)
        if isinstance(node, dict) and str(node.get("id") or node.get("pin_id") or "") == pin_id
    ]
    search_nodes = matching_nodes or objects

    video_url = next((_best_video(node) for node in search_nodes if _best_video(node)), None)
    embedded_images: list[str] = []
    for root in search_nodes:
        for node in _walk(root):
            if not isinstance(node, dict):
                continue
            for key, value in node.items():
                if str(key).lower() not in {"images", "image", "imagespec_orig", "image_url"}:
                    continue
                direct = _url(value)
                image = direct or _best_image(value)
                if image:
                    embedded_images.append(image)
    embedded_images = _dedupe(embedded_images)

    meta_video = next((parser.meta.get(key) for key in (
        "og:video:secure_url", "og:video:url", "og:video", "twitter:player:stream"
    ) if parser.meta.get(key)), None)
    meta_image = next((parser.meta.get(key) for key in (
        "og:image:secure_url", "og:image", "twitter:image"
    ) if parser.meta.get(key)), None)
    video_url = video_url or meta_video
    image_urls = _dedupe(embedded_images or ([meta_image] if meta_image else []))

    caption = parser.meta.get("og:description") or parser.meta.get("description") or ""
    title = parser.meta.get("og:title") or ""
    if not caption and title.lower() != "pinterest":
        caption = title

    author = ""
    for node in matching_nodes:
        pinner = node.get("pinner") or node.get("creator")
        if isinstance(pinner, dict):
            author = str(pinner.get("username") or pinner.get("full_name") or pinner.get("name") or "")
            if author:
                break

    if not video_url and not image_urls:
        raise PinterestNotFound("No downloadable media found for this pin")

    return ExtractedMedia(
        pin_id=pin_id,
        canonical_url=canonical_url,
        caption=caption,
        author=author,
        image_urls=image_urls,
        video_url=video_url,
        thumbnail_url=meta_image or (image_urls[0] if image_urls else None),
    )


def _extract_ytdlp_sync(url: str, pin_id: str) -> ExtractedMedia | None:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "http_headers": {"Referer": "https://www.pinterest.com/"},
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.info("yt-dlp Pinterest extraction failed: %s", exc)
        return None

    if not isinstance(info, dict):
        return None
    entries = info.get("entries")
    if entries:
        info = next((entry for entry in entries if isinstance(entry, dict)), info)

    direct_url = _url(info.get("url"))
    extension = str(info.get("ext") or "").lower()
    video_url = direct_url if extension in {"mp4", "mov", "webm", "m4v"} else None
    image_url = _best_image(info.get("thumbnails"))
    if direct_url and not video_url:
        image_url = direct_url
    if not video_url and not image_url:
        return None
    return ExtractedMedia(
        pin_id=pin_id,
        canonical_url=str(info.get("webpage_url") or url),
        caption=str(info.get("description") or info.get("title") or ""),
        author=str(info.get("uploader") or info.get("channel") or ""),
        image_urls=[] if video_url else [image_url] if image_url else [],
        video_url=video_url,
        thumbnail_url=image_url,
    )


class PinterestExtractor:
    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout

    async def _fetch(self, url: str):
        async with AsyncSession(impersonate="chrome", timeout=self.timeout) as session:
            return await session.get(
                url,
                allow_redirects=True,
                headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": "https://www.pinterest.com/",
                },
            )

    async def extract(self, url: str) -> ExtractedMedia:
        validate_url(url)
        response = await self._fetch(url)
        final_url = str(response.url)
        validate_url(final_url)
        if response.status_code == 404:
            raise PinterestNotFound("Pinterest pin was not found")
        if response.status_code >= 400:
            raise PinterestError(f"Pinterest returned HTTP {response.status_code}")

        pin_id = extract_pin_id(final_url)
        if not pin_id:
            raise PinterestBoardNotSupported("Only individual Pinterest pins are supported")

        try:
            return parse_pin_html(response.text, pin_id, final_url)
        except PinterestNotFound:
            fallback = await asyncio.to_thread(_extract_ytdlp_sync, final_url, pin_id)
            if fallback:
                return fallback
            raise
