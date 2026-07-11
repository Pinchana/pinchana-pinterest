"""Bounded on-disk cache for downloaded pin media."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
from typing import Iterator
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession


class MediaTooLarge(RuntimeError):
    pass


class MediaStorage:
    def __init__(self, base_path: str, max_size_gb: float, max_item_mb: float) -> None:
        if max_size_gb <= 0 or max_item_mb <= 0:
            raise ValueError("Cache and media limits must be positive")
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.max_size = int(max_size_gb * 1024**3)
        self.max_item_size = int(max_item_mb * 1024**2)
        if self.max_item_size > self.max_size:
            raise ValueError("MAX_MEDIA_SIZE_MB cannot exceed CACHE_MAX_SIZE_GB")
        self.lock_path = self.base_path / ".eviction.lock"
        self._cleanup_incomplete()
        self.ensure_space()

    @contextmanager
    def _lock(self) -> Iterator[None]:
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def entry(self, pin_id: str) -> Path:
        return self.base_path / pin_id

    def load(self, pin_id: str) -> dict | None:
        path = self.entry(pin_id) / "metadata.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        paths = data.get("local_files", [])
        if not paths or not all((self.entry(pin_id) / name).is_file() for name in paths):
            return None
        os.utime(path, None)
        return data

    def save(self, pin_id: str, metadata: dict) -> None:
        target = self.entry(pin_id) / "metadata.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)
        self.ensure_space()

    def _size(self) -> int:
        total = 0
        for path in self.base_path.rglob("*"):
            try:
                if path.is_file() and path != self.lock_path:
                    total += path.stat().st_size
            except FileNotFoundError:
                continue
        return total

    def _completed_entries(self) -> list[Path]:
        entries = [path for path in self.base_path.iterdir() if path.is_dir() and (path / "metadata.json").exists()]
        return sorted(entries, key=lambda path: (path / "metadata.json").stat().st_mtime)

    def _cleanup_incomplete(self) -> None:
        """Remove remnants of downloads interrupted before this process started."""
        for path in self.base_path.iterdir():
            if path.is_dir() and not (path / "metadata.json").exists():
                shutil.rmtree(path, ignore_errors=True)

    def ensure_space(self, required: int = 0, exclude: str | None = None) -> None:
        with self._lock():
            while self._size() + required > self.max_size:
                candidates = [path for path in self._completed_entries() if path.name != exclude]
                if not candidates:
                    break
                shutil.rmtree(candidates[0], ignore_errors=True)

    async def download(self, url: str, pin_id: str, filename: str) -> Path:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        if not (host == "pinimg.com" or host.endswith(".pinimg.com")):
            raise RuntimeError("Refusing media URL outside the Pinterest CDN")
        destination = self.entry(pin_id) / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        self.ensure_space(self.max_item_size, exclude=pin_id)
        written = 0
        try:
            async with AsyncSession(impersonate="chrome", timeout=120) as session:
                response = await session.get(
                    url,
                    allow_redirects=True,
                    stream=True,
                    headers={"Referer": "https://www.pinterest.com/"},
                )
                if response.status_code >= 400:
                    raise RuntimeError(f"Media download returned HTTP {response.status_code}")
                final_host = (urlparse(str(response.url)).hostname or "").lower().rstrip(".")
                if not (final_host == "pinimg.com" or final_host.endswith(".pinimg.com")):
                    raise RuntimeError("Pinterest media redirected outside the Pinterest CDN")
                length = int(response.headers.get("content-length") or 0)
                if length > self.max_item_size:
                    raise MediaTooLarge(f"Media exceeds {self.max_item_size // 1024**2} MB limit")
                with temporary.open("wb") as output:
                    async for chunk in response.aiter_content():
                        written += len(chunk)
                        if written > self.max_item_size:
                            raise MediaTooLarge(f"Media exceeds {self.max_item_size // 1024**2} MB limit")
                        output.write(chunk)
            temporary.replace(destination)
            return destination
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
